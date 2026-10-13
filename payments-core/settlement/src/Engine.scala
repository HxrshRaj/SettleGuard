package settlement

import java.time.LocalDate

object Engine:
  private type Key = (String, LocalDate)

  /** Pure fold over events: no I/O, no mutation outside the local builder, order-independent result.
    * Fee is charged per successful charge (percent, half-up, plus fixed) and is NOT returned on refund,
    * mirroring how card processors usually behave.
    */
  def settle(events: Iterable[Event], policy: FeePolicy = FeePolicy.Default): Vector[Line] =
    val grouped = events.foldLeft(Map.empty[Key, Line]) { (acc, e) =>
      val k = (e.merchant, e.day)
      val cur = acc.getOrElse(k, Line(e.merchant, e.day, 0, 0, 0, 0))
      val next = e.kind match
        case "charge.succeeded" =>
          cur.copy(charges = cur.charges + 1, gross = cur.gross + e.amount,
            fees = cur.fees + Rounding.bps(e.amount, policy.percentBps) + policy.fixed)
        case "charge.refunded" => cur.copy(refunds = cur.refunds + e.amount)
        case _                 => cur
      acc.updated(k, next)
    }
    grouped.values.toVector.sortBy(l => (l.merchant, l.day.toEpochDay))

  /** Per-merchant totals across all days, for reconciliation against the ledger's own balances. */
  def merchantNet(lines: Seq[Line]): Map[String, (Long, Long)] =
    lines.groupMapReduce(_.merchant)(l => (l.gross, l.refunds))((a, b) => (a._1 + b._1, a._2 + b._2))

  /** CSV in SettleGuard's `platform_settlements.csv` schema so the reconciliation tool can consume it.
    * amount_inr is the payout in rupees with two decimals; settlement is expected T+2.
    */
  def toPlatformCsv(lines: Seq[Line], policy: FeePolicy = FeePolicy.Default): String =
    val rows = lines.filter(_.payout(policy) > 0).map { l =>
      val p = l.payout(policy)
      val amt = f"${p / 100}%d.${p % 100}%02d"
      s"stl_${l.merchant}_${l.day},${l.merchant},${l.merchant},$amt,${l.day.plusDays(2)}T00:00:00Z,pending"
    }
    ("txn_id,merchant_id,merchant_name,amount_inr,expected_settlement_at,status" +: rows).mkString("", "\n", "\n")

  def toReport(lines: Seq[Line], policy: FeePolicy = FeePolicy.Default): String =
    val head = "merchant,day,charges,gross,refunds,fees,net,reserve,payout"
    val rows = lines.map(l => s"${l.merchant},${l.day},${l.charges},${l.gross},${l.refunds},${l.fees},${l.net},${l.reserve(policy)},${l.payout(policy)}")
    (head +: rows).mkString("", "\n", "\n")
