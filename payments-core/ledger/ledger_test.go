package ledger

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

var fixed = time.Date(2026, 10, 13, 12, 0, 0, 0, time.UTC)

func newTestLedger() *Ledger {
	var n atomic.Int64
	return New(func() time.Time { return fixed }, func(p string) string { return fmt.Sprintf("%s_%d", p, n.Add(1)) })
}

func TestChargeAndRefundKeepJournalBalanced(t *testing.T) {
	l := newTestLedger()
	c, _, err := l.CreateCharge("m1", 10_000, "INR")
	if err != nil || l.Balance("m1") != 10_000 {
		t.Fatalf("charge: %v balance=%d", err, l.Balance("m1"))
	}
	if _, _, err := l.Refund(c.ID, 2_500); err != nil || l.Balance("m1") != 7_500 {
		t.Fatalf("refund: %v balance=%d", err, l.Balance("m1"))
	}
	if err := l.Verify(); err != nil {
		t.Fatal(err)
	}
}

func TestValidation(t *testing.T) {
	l := newTestLedger()
	for _, tc := range []struct {
		m   string
		amt int64
		err error
	}{{"m1", 0, ErrInvalidAmount}, {"m1", -5, ErrInvalidAmount}, {"", 100, ErrInvalidMerchant}, {"bad merchant!", 100, ErrInvalidMerchant}, {"m1", 1 << 62, ErrInvalidAmount}} {
		if _, _, err := l.CreateCharge(tc.m, tc.amt, ""); err != tc.err {
			t.Errorf("%q %d: got %v want %v", tc.m, tc.amt, err, tc.err)
		}
	}
	if len(l.Events()) != 0 || l.Verify() != nil {
		t.Fatal("rejected requests must leave no trace")
	}
}

func TestRefundCannotExceedCharge(t *testing.T) {
	l := newTestLedger()
	c, _, _ := l.CreateCharge("m1", 1_000, "INR")
	if _, _, err := l.Refund(c.ID, 1_001); err != ErrRefundTooLarge {
		t.Fatalf("got %v", err)
	}
	l.Refund(c.ID, 600)
	if _, _, err := l.Refund(c.ID, 401); err != ErrRefundTooLarge {
		t.Fatalf("second refund: %v", err)
	}
	if _, _, err := l.Refund(c.ID, 400); err != nil {
		t.Fatal(err)
	}
	if _, _, err := l.Refund("ch_missing", 1); err != ErrNotFound {
		t.Fatal(err)
	}
	if l.Balance("m1") != 0 {
		t.Fatalf("balance %d", l.Balance("m1"))
	}
}

func TestConcurrentRefundsNeverOverRefund(t *testing.T) {
	l := newTestLedger()
	c, _, _ := l.CreateCharge("m1", 10_000, "INR")
	var wg sync.WaitGroup
	var ok atomic.Int64
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, _, err := l.Refund(c.ID, 300); err == nil {
				ok.Add(1)
			}
		}()
	}
	wg.Wait()
	got, _ := l.GetCharge(c.ID)
	if ok.Load() != 33 || got.Refunded != 9_900 || l.Balance("m1") != 100 {
		t.Fatalf("ok=%d refunded=%d balance=%d", ok.Load(), got.Refunded, l.Balance("m1"))
	}
	if err := l.Verify(); err != nil {
		t.Fatal(err)
	}
}

func TestConcurrentChargesAcrossMerchants(t *testing.T) {
	l := newTestLedger()
	var wg sync.WaitGroup
	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			l.CreateCharge(fmt.Sprintf("m%d", i%4), int64(100+i), "INR")
		}(i)
	}
	wg.Wait()
	var total int64
	for m := 0; m < 4; m++ {
		total += l.Balance(fmt.Sprintf("m%d", m))
	}
	want := int64(0)
	for i := 0; i < 200; i++ {
		want += int64(100 + i)
	}
	if total != want || l.Verify() != nil || len(l.Events()) != 200 {
		t.Fatalf("total=%d want=%d events=%d", total, want, len(l.Events()))
	}
}

// ---- idempotency -------------------------------------------------------------- //
func newTestServer() (*Server, *Ledger) {
	l := newTestLedger()
	d := NewDispatcher(func() time.Time { return fixed }, func(time.Duration) {})
	return NewServer(l, d), l
}

func do(h http.Handler, method, path, key, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if key != "" {
		req.Header.Set("Idempotency-Key", key)
	}
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	return rr
}

func TestIdempotencyReplayConflictAndMissingKey(t *testing.T) {
	s, l := newTestServer()
	h := s.Handler()
	body := `{"merchant_id":"m1","amount":5000}`
	a := do(h, "POST", "/v1/charges", "k1", body)
	b := do(h, "POST", "/v1/charges", "k1", body)
	if a.Code != 201 || b.Code != 201 || a.Body.String() != b.Body.String() || b.Header().Get("Idempotent-Replayed") != "true" {
		t.Fatalf("replay: %d %d\n%s\n%s", a.Code, b.Code, a.Body, b.Body)
	}
	if len(l.Events()) != 1 || l.Balance("m1") != 5000 {
		t.Fatal("replay must not create a second charge")
	}
	if c := do(h, "POST", "/v1/charges", "k1", `{"merchant_id":"m1","amount":9999}`); c.Code != 422 {
		t.Fatalf("conflict: %d", c.Code)
	}
	if c := do(h, "POST", "/v1/charges", "", body); c.Code != 400 {
		t.Fatalf("missing key: %d", c.Code)
	}
}

func TestConcurrentDuplicateRequestsExecuteOnce(t *testing.T) {
	s, l := newTestServer()
	h := s.Handler()
	var wg sync.WaitGroup
	bodies := make([]string, 20)
	for i := range bodies {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			bodies[i] = do(h, "POST", "/v1/charges", "same-key", `{"merchant_id":"m1","amount":700}`).Body.String()
		}(i)
	}
	wg.Wait()
	for _, b := range bodies {
		if b != bodies[0] {
			t.Fatal("all duplicates must see the same response")
		}
	}
	if len(l.Events()) != 1 || l.Balance("m1") != 700 {
		t.Fatalf("executed %d times", len(l.Events()))
	}
}

func TestHTTPFlow(t *testing.T) {
	s, _ := newTestServer()
	h := s.Handler()
	var c Charge
	json.Unmarshal(do(h, "POST", "/v1/charges", "a", `{"merchant_id":"m1","amount":1000}`).Body.Bytes(), &c)
	if r := do(h, "POST", "/v1/charges/"+c.ID+"/refunds", "b", `{"amount":400}`); r.Code != 201 {
		t.Fatalf("refund %d %s", r.Code, r.Body)
	}
	if r := do(h, "POST", "/v1/charges/"+c.ID+"/refunds", "c", `{"amount":601}`); r.Code != 400 {
		t.Fatalf("over-refund %d", r.Code)
	}
	if r := do(h, "POST", "/v1/charges/ch_nope/refunds", "d", `{"amount":1}`); r.Code != 404 {
		t.Fatalf("unknown charge %d", r.Code)
	}
	if r := do(h, "GET", "/v1/charges/"+c.ID, "", ""); !strings.Contains(r.Body.String(), `"amount_refunded":400`) {
		t.Fatal(r.Body.String())
	}
	if r := do(h, "GET", "/v1/balances/m1", "", ""); !strings.Contains(r.Body.String(), `"available":600`) {
		t.Fatal(r.Body.String())
	}
	csv := do(h, "GET", "/v1/events.csv", "", "").Body.String()
	if lines := strings.Split(strings.TrimSpace(csv), "\n"); len(lines) != 3 || !strings.HasPrefix(lines[0], "event_id,type,charge_id") {
		t.Fatalf("csv:\n%s", csv)
	}
	if r := do(h, "POST", "/v1/charges", "e", "not json"); r.Code != 400 {
		t.Fatal(r.Code)
	}
}

// ---- webhooks ----------------------------------------------------------------- //
func TestSignMatchesIndependentHMAC(t *testing.T) {
	// Expected value computed separately with Python's hmac module, so this is not a self-fulfilling test.
	got := Sign("whsec_test_1234", []byte(`{"id":"evt_1"}`), time.Unix(1700000000, 0))
	want := "t=1700000000,v1=" + "44c25592356f1bebecfd3f032d26e162c59504cb5cdbf7938344b38a924f36a8"
	if got != want {
		t.Fatalf("got %s want %s", got, want)
	}
}

func TestDispatcherRetriesWithBackoffThenSucceeds(t *testing.T) {
	var hits atomic.Int64
	var sigs []string
	var mu sync.Mutex
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		sigs = append(sigs, r.Header.Get("Ledger-Signature"))
		mu.Unlock()
		if hits.Add(1) <= 2 {
			w.WriteHeader(503)
			return
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()
	var waits []time.Duration
	var wmu sync.Mutex
	d := NewDispatcher(time.Now, func(x time.Duration) { wmu.Lock(); waits = append(waits, x); wmu.Unlock() })
	d.Backoff = time.Second
	d.Register(Endpoint{ID: "we_1", URL: srv.URL, Secret: "secret-12345"})
	d.Dispatch(Event{ID: "evt_1", Type: "charge.succeeded"})
	d.Wait()
	dl := d.Deliveries()[0]
	if !dl.Delivered || dl.Attempts != 3 || len(waits) != 2 || waits[0] != time.Second || waits[1] != 2*time.Second {
		t.Fatalf("%+v waits=%v", dl, waits)
	}
	for _, s := range sigs {
		if !strings.HasPrefix(s, "t=") || !strings.Contains(s, ",v1=") {
			t.Fatalf("bad signature header %q", s)
		}
	}
}

func TestDispatcherDoesNotRetryClientErrorsAndGivesUp(t *testing.T) {
	var four, five atomic.Int64
	s4 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { four.Add(1); w.WriteHeader(400) }))
	s5 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { five.Add(1); w.WriteHeader(500) }))
	defer s4.Close()
	defer s5.Close()
	d := NewDispatcher(time.Now, func(time.Duration) {})
	d.MaxAttempts = 4
	d.Register(Endpoint{ID: "a", URL: s4.URL, Secret: "secret-12345"})
	d.Register(Endpoint{ID: "b", URL: s5.URL, Secret: "secret-12345"})
	d.Dispatch(Event{ID: "evt_2"})
	d.Wait()
	if four.Load() != 1 || five.Load() != 4 {
		t.Fatalf("4xx hits=%d (want 1), 5xx hits=%d (want 4)", four.Load(), five.Load())
	}
	for _, dl := range d.Deliveries() {
		if dl.Delivered {
			t.Fatalf("should not be delivered: %+v", dl)
		}
	}
}

func TestRegisterEndpointValidation(t *testing.T) {
	s, _ := newTestServer()
	h := s.Handler()
	for _, body := range []string{`{"url":"ftp://x","secret":"secret-12345"}`, `{"url":"http://ok.test","secret":"short"}`, `{"url":"not a url","secret":"secret-12345"}`, `{}`} {
		if r := do(h, "POST", "/v1/webhook_endpoints", "", body); r.Code != 400 {
			t.Errorf("%s -> %d", body, r.Code)
		}
	}
	r := do(h, "POST", "/v1/webhook_endpoints", "", `{"url":"http://ok.test/hook","secret":"secret-12345"}`)
	if r.Code != 201 || bytes.Contains(r.Body.Bytes(), []byte("secret-12345")) {
		t.Fatalf("%d %s (the secret must never be echoed back)", r.Code, r.Body)
	}
}
