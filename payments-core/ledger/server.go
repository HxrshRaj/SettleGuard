package ledger

import (
	"crypto/rand"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type Server struct {
	L    *Ledger
	Idem *Idempotency
	D    *Dispatcher
	mux  *http.ServeMux
}

func RandomID(prefix string) string {
	b := make([]byte, 9)
	_, _ = rand.Read(b)
	return prefix + "_" + hex.EncodeToString(b)
}

func NewServer(l *Ledger, d *Dispatcher) *Server {
	s := &Server{L: l, Idem: NewIdempotency(), D: d, mux: http.NewServeMux()}
	s.mux.HandleFunc("POST /v1/charges", s.idempotent(s.createCharge))
	s.mux.HandleFunc("GET /v1/charges/{id}", s.getCharge)
	s.mux.HandleFunc("POST /v1/charges/{id}/refunds", s.idempotent(s.refund))
	s.mux.HandleFunc("GET /v1/balances/{merchant}", s.balance)
	s.mux.HandleFunc("GET /v1/events.csv", s.exportCSV)
	s.mux.HandleFunc("POST /v1/webhook_endpoints", s.registerEndpoint)
	s.mux.HandleFunc("GET /v1/deliveries", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, s.D.Deliveries()) })
	s.mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, map[string]string{"status": "ok"}) })
	return s
}

func (s *Server) Handler() http.Handler { return s.mux }

type apiError struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, code int, typ, msg string) {
	writeJSON(w, code, map[string]apiError{"error": {Type: typ, Message: msg}})
}

type recorder struct {
	http.ResponseWriter
	status int
	body   []byte
}

func (r *recorder) WriteHeader(c int) { r.status = c; r.ResponseWriter.WriteHeader(c) }
func (r *recorder) Write(b []byte) (int, error) {
	r.body = append(r.body, b...)
	return r.ResponseWriter.Write(b)
}

// idempotent wraps a mutating handler so retries with the same Idempotency-Key never repeat the side effect.
func (s *Server) idempotent(h func(http.ResponseWriter, *http.Request, []byte)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("Idempotency-Key")
		if key == "" || len(key) > 255 {
			writeErr(w, 400, "invalid_request", "Idempotency-Key header is required (max 255 characters)")
			return
		}
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64<<10))
		if err != nil {
			writeErr(w, 413, "invalid_request", "request body too large")
			return
		}
		switch out, status, stored := s.Idem.Begin(key, r.Method, r.URL.Path, body); out {
		case Conflict:
			writeErr(w, 422, "idempotency_error", "this Idempotency-Key was already used with different request parameters")
		case Replayed:
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Idempotent-Replayed", "true")
			w.WriteHeader(status)
			_, _ = w.Write(stored)
		default:
			rec := &recorder{ResponseWriter: w, status: 200}
			defer func() { s.Idem.Finish(key, rec.status, rec.body) }()
			h(rec, r, body)
		}
	}
}

func (s *Server) createCharge(w http.ResponseWriter, r *http.Request, body []byte) {
	var in struct {
		MerchantID string `json:"merchant_id"`
		Amount     int64  `json:"amount"`
		Currency   string `json:"currency"`
	}
	if err := json.Unmarshal(body, &in); err != nil {
		writeErr(w, 400, "invalid_request", "body must be JSON")
		return
	}
	c, ev, err := s.L.CreateCharge(in.MerchantID, in.Amount, in.Currency)
	if err != nil {
		writeErr(w, 400, "invalid_request", err.Error())
		return
	}
	s.D.Dispatch(ev)
	writeJSON(w, 201, c)
}

func (s *Server) getCharge(w http.ResponseWriter, r *http.Request) {
	c, err := s.L.GetCharge(r.PathValue("id"))
	if err != nil {
		writeErr(w, 404, "not_found", err.Error())
		return
	}
	writeJSON(w, 200, c)
}

func (s *Server) refund(w http.ResponseWriter, r *http.Request, body []byte) {
	var in struct {
		Amount int64 `json:"amount"`
	}
	if err := json.Unmarshal(body, &in); err != nil {
		writeErr(w, 400, "invalid_request", "body must be JSON")
		return
	}
	re, ev, err := s.L.Refund(r.PathValue("id"), in.Amount)
	switch {
	case errors.Is(err, ErrNotFound):
		writeErr(w, 404, "not_found", err.Error())
	case errors.Is(err, ErrRefundTooLarge), errors.Is(err, ErrInvalidAmount):
		writeErr(w, 400, "invalid_request", err.Error())
	case err != nil:
		writeErr(w, 500, "api_error", "internal error")
	default:
		s.D.Dispatch(ev)
		writeJSON(w, 201, re)
	}
}

func (s *Server) balance(w http.ResponseWriter, r *http.Request) {
	m := r.PathValue("merchant")
	writeJSON(w, 200, map[string]any{"merchant_id": m, "available": s.L.Balance(m), "currency": "INR"})
}

// exportCSV is the contract with the Scala settlement engine: event_id,type,charge_id,merchant_id,amount,currency,created_at
func (s *Server) exportCSV(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/csv")
	cw := csv.NewWriter(w)
	_ = cw.Write([]string{"event_id", "type", "charge_id", "merchant_id", "amount", "currency", "created_at"})
	for _, e := range s.L.Events() {
		_ = cw.Write([]string{e.ID, e.Type, e.ChargeID, e.MerchantID, strconv.FormatInt(e.Amount, 10), e.Currency, e.CreatedAt.Format(time.RFC3339)})
	}
	cw.Flush()
}

func (s *Server) registerEndpoint(w http.ResponseWriter, r *http.Request) {
	var in struct {
		URL    string `json:"url"`
		Secret string `json:"secret"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4<<10)).Decode(&in); err != nil || len(in.Secret) < 8 {
		writeErr(w, 400, "invalid_request", "url and a secret of at least 8 characters are required")
		return
	}
	u, err := url.Parse(in.URL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || strings.ContainsAny(in.URL, " \n") {
		writeErr(w, 400, "invalid_request", "url must be an absolute http(s) URL")
		return
	}
	ep := Endpoint{ID: RandomID("we"), URL: in.URL, Secret: in.Secret}
	s.D.Register(ep)
	writeJSON(w, 201, ep)
}
