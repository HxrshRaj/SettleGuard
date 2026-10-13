package settlement

import java.time.{Instant, LocalDate, ZoneOffset}

/** Amounts are always integer minor units (paise); no floating point touches money. */
final case class Event(id: String, kind: String, chargeId: String, merchant: String,
                       amount: Long, currency: String, createdAt: Instant):
  def day: LocalDate = createdAt.atOffset(ZoneOffset.UTC).toLocalDate

final case class FeePolicy(percentBps: Long, fixed: Long, reserveBps: Long):
  require(percentBps >= 0 && percentBps <= 10000 && fixed >= 0 && reserveBps >= 0 && reserveBps <= 10000)

object FeePolicy:
  /** 2% + 3.00 INR, with a 5% rolling reserve. Illustrative numbers, not Stripe's pricing. */
  val Default: FeePolicy = FeePolicy(percentBps = 200, fixed = 300, reserveBps = 500)

/** One merchant, one UTC day. */
final case class Line(merchant: String, day: LocalDate, charges: Long, gross: Long,
                      refunds: Long, fees: Long):
  def net: Long = gross - refunds - fees
  /** Reserve is held back only from positive net. */
  def reserve(p: FeePolicy): Long = if net > 0 then Rounding.bps(net, p.reserveBps) else 0L
  def payout(p: FeePolicy): Long = net - reserve(p)

object Rounding:
  /** floor(x * bps / 10000 + 1/2) for non-negative x, in integer arithmetic. */
  def bps(x: Long, bps: Long): Long =
    require(x >= 0, "bps rounding expects a non-negative amount")
    Math.multiplyExact(x, bps).+(5000L) / 10000L
