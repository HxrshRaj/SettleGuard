package settlement

import java.time.{Instant, LocalDate}
import scala.util.Random

/** Tiny dependency-free harness (scalatest/munit can't be fetched in this environment). Exit code 1 on failure. */
object Tests:
  private var failures = 0
  private var count = 0
  def test(name: String)(body: => Unit): Unit =
    count += 1
    try { body; println(s"  ok   $name") }
    catch case e: Throwable => { failures += 1; println(s"  FAIL $name: ${e.getMessage}") }
  def check(cond: Boolean, msg: => String = "assertion failed"): Unit = if !cond then throw new AssertionError(msg)
  def eq[A](got: A, want: A): Unit = check(got == want, s"got $got, want $want")

  private def ev(i: Int, kind: String, m: String, amt: Long, at: String) =
    Event(s"evt_$i", kind, s"ch_$i", m, amt, "INR", Instant.parse(at))

  def main(args: Array[String]): Unit =
    println("settlement tests")

    test("half-up rounding on percentage fee") {
      eq(Rounding.bps(1000, 200), 20L)       // exactly 20
      eq(Rounding.bps(250, 200), 5L)         // 5.0
      eq(Rounding.bps(225, 200), 5L)         // 4.5 -> 5 (half up)
      eq(Rounding.bps(224, 200), 4L)         // 4.48 -> 4
      eq(Rounding.bps(0, 200), 0L)
    }

    test("overflow is detected, not wrapped") {
      var threw = false
      try Rounding.bps(Long.MaxValue, 10000) catch case _: ArithmeticException => threw = true
      check(threw)
    }

    test("single charge produces fee, net, reserve and payout") {
      val l = Engine.settle(Seq(ev(1, "charge.succeeded", "m1", 10000, "2026-10-13T10:00:00Z"))).head
      eq(l.fees, 500L)                       // 2% of 10000 = 200, + 300 fixed
      eq(l.net, 9500L)
      eq(l.reserve(FeePolicy.Default), 475L) // 5% of 9500
      eq(l.payout(FeePolicy.Default), 9025L)
    }

    test("refund reduces net but fee is kept") {
      val l = Engine.settle(Seq(
        ev(1, "charge.succeeded", "m1", 10000, "2026-10-13T10:00:00Z"),
        ev(2, "charge.refunded", "m1", 4000, "2026-10-13T11:00:00Z"))).head
      eq(l.net, 10000L - 4000L - 500L)
    }

    test("grouped by merchant and UTC day, including day boundary") {
      val lines = Engine.settle(Seq(
        ev(1, "charge.succeeded", "m1", 100, "2026-10-13T23:59:59Z"),
        ev(2, "charge.succeeded", "m1", 100, "2026-10-14T00:00:00Z"),
        ev(3, "charge.succeeded", "m2", 100, "2026-10-13T00:00:00Z")))
      eq(lines.map(l => (l.merchant, l.day.toString)),
        Vector(("m1", "2026-10-13"), ("m1", "2026-10-14"), ("m2", "2026-10-13")))
    }

    test("negative net holds no reserve") {
      val l = Engine.settle(Seq(
        ev(1, "charge.succeeded", "m1", 1000, "2026-10-13T10:00:00Z"),
        ev(2, "charge.refunded", "m1", 1000, "2026-10-13T10:05:00Z"))).head
      check(l.net < 0)
      eq(l.reserve(FeePolicy.Default), 0L)
    }

    test("CSV: quoted fields, bad rows reported with line numbers, header validated") {
      eq(Csv.splitLine("a,\"b,c\",\"d\"\"e\""), Vector("a", "b,c", "d\"e"))
      val text = Csv.Header.mkString(",") + "\n" +
        "e1,charge.succeeded,c1,m1,500,INR,2026-10-13T10:00:00Z\n" +
        "e2,charge.succeeded,c2,m1,abc,INR,2026-10-13T10:00:00Z\n" +
        "e3,charge.exploded,c3,m1,5,INR,2026-10-13T10:00:00Z\n" +
        "e4,charge.succeeded,c4,m1,5,INR,yesterday\n" +
        "e5,charge.succeeded,c5\n"
      val (good, bad) = Csv.parseEvents(text)
      eq(good.map(_.id), Vector("e1"))
      eq(bad.map(_._1), Vector(3, 4, 5, 6))
      eq(Csv.parseEvents("wrong,header\n")._2.size, 1)
    }

    test("platform CSV matches SettleGuard schema and formats rupees from paise") {
      val lines = Engine.settle(Seq(ev(1, "charge.succeeded", "m1", 10000, "2026-10-13T10:00:00Z")))
      val out = Engine.toPlatformCsv(lines).linesIterator.toVector
      eq(out.head, "txn_id,merchant_id,merchant_name,amount_inr,expected_settlement_at,status")
      eq(out(1), "stl_m1_2026-10-13,m1,m1,90.25,2026-10-15T00:00:00Z,pending")
    }

    // ---- randomized invariants (fixed seed => reproducible) -------------------- //
    val rnd = new Random(20261013)
    def randomEvents(n: Int): Vector[Event] =
      val base = Instant.parse("2026-10-10T00:00:00Z").getEpochSecond
      (0 until n).toVector.map { i =>
        val kind = if rnd.nextInt(4) == 0 then "charge.refunded" else "charge.succeeded"
        Event(s"e$i", kind, s"c$i", s"m${rnd.nextInt(5)}", 1L + rnd.nextInt(500000), "INR",
          Instant.ofEpochSecond(base + rnd.nextInt(5 * 86400)))
      }

    test("invariant: result is independent of event order (200 shuffles)") {
      (1 to 200).foreach { _ =>
        val es = randomEvents(60)
        eq(Engine.settle(rnd.shuffle(es)), Engine.settle(es))
      }
    }

    test("invariant: money is conserved (net = gross - refunds - fees; payout + reserve = net)") {
      (1 to 200).foreach { _ =>
        val es = randomEvents(80)
        val lines = Engine.settle(es)
        val p = FeePolicy.Default
        eq(lines.map(_.gross).sum, es.filter(_.kind == "charge.succeeded").map(_.amount).sum)
        eq(lines.map(_.refunds).sum, es.filter(_.kind == "charge.refunded").map(_.amount).sum)
        lines.foreach(l => eq(l.payout(p) + l.reserve(p), l.net))
        check(lines.forall(l => l.reserve(p) >= 0 && l.reserve(p) <= math.max(0L, l.net)))
      }
    }

    test("invariant: splitting the stream and merging per-key results equals one pass") {
      (1 to 100).foreach { _ =>
        val es = randomEvents(70)
        val (a, b) = es.splitAt(rnd.nextInt(70))
        val merged = (Engine.settle(a) ++ Engine.settle(b)).groupBy(l => (l.merchant, l.day)).view.mapValues(ls =>
          ls.reduce((x, y) => x.copy(charges = x.charges + y.charges, gross = x.gross + y.gross,
            refunds = x.refunds + y.refunds, fees = x.fees + y.fees))).values.toVector
        eq(merged.sortBy(l => (l.merchant, l.day.toEpochDay)), Engine.settle(es))
      }
    }

    println(s"$count tests, $failures failed")
    if failures > 0 then sys.exit(1)
