package main

import (
	"flag"
	"log"
	"net/http"
	"time"

	ledger "github.com/HxrshRaj/settleguard/payments-core/ledger"
)

func main() {
	addr := flag.String("addr", ":8090", "listen address")
	flag.Parse()
	l := ledger.New(time.Now, ledger.RandomID)
	d := ledger.NewDispatcher(time.Now, time.Sleep)
	srv := &http.Server{Addr: *addr, Handler: ledger.NewServer(l, d).Handler(), ReadHeaderTimeout: 5 * time.Second}
	log.Printf("ledgerd listening on %s (in-memory store: data is lost on restart)", *addr)
	log.Fatal(srv.ListenAndServe())
}
