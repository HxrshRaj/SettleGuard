package ledger

import (
	"crypto/sha256"
	"encoding/hex"
	"sync"
)

// Idempotency stores the first response for each key. Replaying the same key with the same request returns
// the stored response; the same key with a different request is a conflict. This is what makes client
// retries after a network failure safe: the payment is created at most once.
type Idempotency struct {
	mu      sync.Mutex
	entries map[string]*idemEntry
}

type idemEntry struct {
	fingerprint string
	done        chan struct{} // closed when the first request finishes
	status      int
	body        []byte
}

func NewIdempotency() *Idempotency { return &Idempotency{entries: map[string]*idemEntry{}} }

func fingerprint(method, path string, body []byte) string {
	h := sha256.New()
	h.Write([]byte(method + "\n" + path + "\n"))
	h.Write(body)
	return hex.EncodeToString(h.Sum(nil))
}

type Outcome int

const (
	First    Outcome = iota // caller must execute the request and call Finish
	Replayed                // stored response returned
	Conflict                // same key, different request
)

// Begin claims the key. Concurrent duplicates wait for the first request to finish, then replay its result.
func (i *Idempotency) Begin(key, method, path string, body []byte) (Outcome, int, []byte) {
	fp := fingerprint(method, path, body)
	i.mu.Lock()
	e, ok := i.entries[key]
	if !ok {
		i.entries[key] = &idemEntry{fingerprint: fp, done: make(chan struct{})}
		i.mu.Unlock()
		return First, 0, nil
	}
	i.mu.Unlock()
	if e.fingerprint != fp {
		return Conflict, 0, nil
	}
	<-e.done
	return Replayed, e.status, e.body
}

func (i *Idempotency) Finish(key string, status int, body []byte) {
	i.mu.Lock()
	e := i.entries[key]
	i.mu.Unlock()
	e.status, e.body = status, body
	close(e.done)
}
