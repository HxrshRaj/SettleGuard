// Package ledger is a small double-entry payments ledger.
//
// Money is always an int64 count of minor units (paise), never a float. Every charge and refund posts
// balanced journal entries, so the sum of all entries is always zero; Verify() checks that invariant.
package ledger

import (
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"
)

var (
	ErrInvalidAmount   = errors.New("amount must be a positive integer number of minor units")
	ErrInvalidMerchant = errors.New("merchant_id is required (letters, digits, - and _ only)")
	ErrNotFound        = errors.New("charge not found")
	ErrRefundTooLarge  = errors.New("refund exceeds the remaining refundable amount")
)

type Charge struct {
	ID         string    `json:"id"`
	MerchantID string    `json:"merchant_id"`
	Amount     int64     `json:"amount"`
	Currency   string    `json:"currency"`
	Refunded   int64     `json:"amount_refunded"`
	CreatedAt  time.Time `json:"created_at"`
}

type Refund struct {
	ID        string    `json:"id"`
	ChargeID  string    `json:"charge_id"`
	Amount    int64     `json:"amount"`
	CreatedAt time.Time `json:"created_at"`
}

// Entry is one leg of a journal transaction. Positive = debit, negative = credit.
type Entry struct {
	TxID    string
	Account string
	Amount  int64
}

// Event is the immutable audit record exported for downstream systems (the Scala settlement engine).
type Event struct {
	ID         string    `json:"id"`
	Type       string    `json:"type"` // charge.succeeded | charge.refunded
	ChargeID   string    `json:"charge_id"`
	MerchantID string    `json:"merchant_id"`
	Amount     int64     `json:"amount"`
	Currency   string    `json:"currency"`
	CreatedAt  time.Time `json:"created_at"`
}

type Ledger struct {
	mu      sync.Mutex
	now     func() time.Time
	newID   func(prefix string) string
	charges map[string]*Charge
	entries []Entry
	events  []Event
}

func New(now func() time.Time, newID func(prefix string) string) *Ledger {
	return &Ledger{now: now, newID: newID, charges: map[string]*Charge{}}
}

func validMerchant(s string) bool {
	if s == "" || len(s) > 64 {
		return false
	}
	for _, c := range s {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_') {
			return false
		}
	}
	return true
}

func (l *Ledger) post(txID string, legs ...Entry) {
	for _, e := range legs {
		e.TxID = txID
		l.entries = append(l.entries, e)
	}
}

// CreateCharge records a successful charge: customer funds are debited, the merchant payable credited.
func (l *Ledger) CreateCharge(merchant string, amount int64, currency string) (Charge, Event, error) {
	if amount <= 0 || amount > 1_000_000_000_00 {
		return Charge{}, Event{}, ErrInvalidAmount
	}
	if !validMerchant(merchant) {
		return Charge{}, Event{}, ErrInvalidMerchant
	}
	if currency == "" {
		currency = "INR"
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	c := &Charge{ID: l.newID("ch"), MerchantID: merchant, Amount: amount, Currency: currency, CreatedAt: l.now().UTC()}
	l.charges[c.ID] = c
	l.post(c.ID, Entry{Account: "customer_funds", Amount: amount}, Entry{Account: "payable:" + merchant, Amount: -amount})
	ev := Event{ID: l.newID("evt"), Type: "charge.succeeded", ChargeID: c.ID, MerchantID: merchant, Amount: amount, Currency: currency, CreatedAt: c.CreatedAt}
	l.events = append(l.events, ev)
	return *c, ev, nil
}

func (l *Ledger) GetCharge(id string) (Charge, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	c, ok := l.charges[id]
	if !ok {
		return Charge{}, ErrNotFound
	}
	return *c, nil
}

// Refund reverses part (or all) of a charge. The check and the update happen under one lock, so two
// concurrent refunds can never together exceed the original amount.
func (l *Ledger) Refund(chargeID string, amount int64) (Refund, Event, error) {
	if amount <= 0 {
		return Refund{}, Event{}, ErrInvalidAmount
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	c, ok := l.charges[chargeID]
	if !ok {
		return Refund{}, Event{}, ErrNotFound
	}
	if c.Refunded+amount > c.Amount {
		return Refund{}, Event{}, ErrRefundTooLarge
	}
	c.Refunded += amount
	r := Refund{ID: l.newID("re"), ChargeID: chargeID, Amount: amount, CreatedAt: l.now().UTC()}
	l.post(r.ID, Entry{Account: "payable:" + c.MerchantID, Amount: amount}, Entry{Account: "customer_funds", Amount: -amount})
	ev := Event{ID: l.newID("evt"), Type: "charge.refunded", ChargeID: chargeID, MerchantID: c.MerchantID, Amount: amount, Currency: c.Currency, CreatedAt: r.CreatedAt}
	l.events = append(l.events, ev)
	return r, ev, nil
}

// Balance is what the platform owes the merchant (credits minus debits on its payable account), in minor units.
func (l *Ledger) Balance(merchant string) int64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	var sum int64
	for _, e := range l.entries {
		if e.Account == "payable:"+merchant {
			sum -= e.Amount
		}
	}
	return sum
}

func (l *Ledger) Events() []Event {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]Event(nil), l.events...)
}

// Verify checks the double-entry invariants: the whole journal sums to zero and so does every transaction.
func (l *Ledger) Verify() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	var total int64
	byTx := map[string]int64{}
	for _, e := range l.entries {
		total += e.Amount
		byTx[e.TxID] += e.Amount
	}
	if total != 0 {
		return fmt.Errorf("journal does not balance: %d", total)
	}
	txs := make([]string, 0, len(byTx))
	for t := range byTx {
		txs = append(txs, t)
	}
	sort.Strings(txs)
	for _, t := range txs {
		if byTx[t] != 0 {
			return fmt.Errorf("transaction %s is unbalanced: %d", t, byTx[t])
		}
	}
	return nil
}
