package settlement

import java.time.Instant
import scala.util.Try

object Csv:
  /** RFC-4180 field splitter (quotes, doubled quotes). Single-line records only, which is all the ledger emits. */
  def splitLine(line: String): Vector[String] =
    val out = Vector.newBuilder[String]
    val cur = new StringBuilder
    var inQuotes = false
    var i = 0
    while i < line.length do
      val c = line.charAt(i)
      if inQuotes then
        if c == '"' && i + 1 < line.length && line.charAt(i + 1) == '"' then { cur += '"'; i += 1 }
        else if c == '"' then inQuotes = false
        else cur += c
      else c match
        case '"' => inQuotes = true
        case ',' => out += cur.toString; cur.clear()
        case _   => cur += c
      i += 1
    out += cur.toString
    out.result()

  val Header = Vector("event_id", "type", "charge_id", "merchant_id", "amount", "currency", "created_at")

  /** Returns parsed events plus (lineNumber, reason) for every row that was rejected. */
  def parseEvents(text: String): (Vector[Event], Vector[(Int, String)]) =
    val lines = text.linesIterator.toVector.zipWithIndex.filter(_._1.trim.nonEmpty)
    lines.headOption match
      case None => (Vector.empty, Vector.empty)
      case Some((h, _)) if splitLine(h) != Header =>
        (Vector.empty, Vector(1 -> s"unexpected header: $h"))
      case Some(_) =>
        val results = lines.tail.map { (l, idx) => (idx + 1) -> parseRow(splitLine(l)) }
        (results.collect { case (_, Right(e)) => e }, results.collect { case (n, Left(r)) => n -> r })

  private def parseRow(f: Vector[String]): Either[String, Event] =
    if f.length != 7 then Left(s"expected 7 fields, got ${f.length}")
    else
      for
        amount <- Try(f(4).toLong).toEither.left.map(_ => s"bad amount '${f(4)}'")
        _      <- Either.cond(amount > 0, (), s"non-positive amount $amount")
        kind   <- Either.cond(f(1) == "charge.succeeded" || f(1) == "charge.refunded", f(1), s"unknown type '${f(1)}'")
        at     <- Try(Instant.parse(f(6))).toEither.left.map(_ => s"bad timestamp '${f(6)}'")
      yield Event(f(0), kind, f(2), f(3), amount, f(5), at)
