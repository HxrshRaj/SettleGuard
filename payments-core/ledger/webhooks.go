package ledger

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// Sign returns a Stripe-style header value "t=<unix>,v1=<hex>" where v1 = HMAC-SHA256(secret, "<t>.<payload>").
// The timestamp is inside the signed string so an old captured request cannot be replayed with a new time.
func Sign(secret string, payload []byte, t time.Time) string {
	ts := strconv.FormatInt(t.Unix(), 10)
	m := hmac.New(sha256.New, []byte(secret))
	m.Write([]byte(ts + "."))
	m.Write(payload)
	return fmt.Sprintf("t=%s,v1=%s", ts, hex.EncodeToString(m.Sum(nil)))
}

type Endpoint struct {
	ID     string `json:"id"`
	URL    string `json:"url"`
	Secret string `json:"-"`
}

type Delivery struct {
	EndpointID string `json:"endpoint_id"`
	EventID    string `json:"event_id"`
	Attempts   int    `json:"attempts"`
	Delivered  bool   `json:"delivered"`
	LastStatus int    `json:"last_status"`
}

// Dispatcher delivers events to registered endpoints with retries and exponential backoff.
type Dispatcher struct {
	mu          sync.Mutex
	endpoints   []Endpoint
	deliveries  []*Delivery
	client      *http.Client
	now         func() time.Time
	sleep       func(time.Duration)
	MaxAttempts int
	Backoff     time.Duration
	wg          sync.WaitGroup
}

func NewDispatcher(now func() time.Time, sleep func(time.Duration)) *Dispatcher {
	return &Dispatcher{client: &http.Client{Timeout: 3 * time.Second}, now: now, sleep: sleep, MaxAttempts: 5, Backoff: 500 * time.Millisecond}
}

func (d *Dispatcher) Register(e Endpoint) {
	d.mu.Lock()
	d.endpoints = append(d.endpoints, e)
	d.mu.Unlock()
}

func (d *Dispatcher) Dispatch(ev Event) {
	d.mu.Lock()
	eps := append([]Endpoint(nil), d.endpoints...)
	d.mu.Unlock()
	payload, _ := json.Marshal(ev)
	for _, ep := range eps {
		dl := &Delivery{EndpointID: ep.ID, EventID: ev.ID}
		d.mu.Lock()
		d.deliveries = append(d.deliveries, dl)
		d.mu.Unlock()
		d.wg.Add(1)
		go func(ep Endpoint) {
			defer d.wg.Done()
			d.deliver(ep, payload, dl)
		}(ep)
	}
}

func (d *Dispatcher) deliver(ep Endpoint, payload []byte, dl *Delivery) {
	for attempt := 1; attempt <= d.MaxAttempts; attempt++ {
		req, _ := http.NewRequest(http.MethodPost, ep.URL, bytes.NewReader(payload))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Ledger-Signature", Sign(ep.Secret, payload, d.now())) // re-signed per attempt: fresh timestamp
		status := 0
		resp, err := d.client.Do(req)
		if err == nil {
			status = resp.StatusCode
			_, _ = io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
		}
		d.mu.Lock()
		dl.Attempts, dl.LastStatus = attempt, status
		ok := status >= 200 && status < 300
		dl.Delivered = ok
		d.mu.Unlock()
		if ok || (status >= 400 && status < 500 && status != 429) { // client errors are not retried
			return
		}
		if attempt < d.MaxAttempts {
			d.sleep(d.Backoff << (attempt - 1))
		}
	}
}

func (d *Dispatcher) Wait() { d.wg.Wait() }

func (d *Dispatcher) Deliveries() []Delivery {
	d.mu.Lock()
	defer d.mu.Unlock()
	out := make([]Delivery, len(d.deliveries))
	for i, x := range d.deliveries {
		out[i] = *x
	}
	return out
}
