package settlement

import java.nio.file.{Files, Paths}

/** usage: settle <events.csv> [--report out.csv] [--platform out.csv] [--totals] */
@main def settle(args: String*): Unit =
  if args.isEmpty then
    Console.err.println("usage: settle <events.csv> [--report out.csv] [--platform out.csv] [--totals]")
    sys.exit(2)
  val (events, bad) = Csv.parseEvents(Files.readString(Paths.get(args.head)))
  bad.foreach((n, why) => Console.err.println(s"skipped line $n: $why"))
  val lines = Engine.settle(events)
  def opt(flag: String) = args.indexOf(flag) match { case -1 => None; case i => args.lift(i + 1) }
  opt("--report").foreach(p => Files.writeString(Paths.get(p), Engine.toReport(lines)))
  opt("--platform").foreach(p => Files.writeString(Paths.get(p), Engine.toPlatformCsv(lines)))
  if args.contains("--totals") then
    // machine-readable: merchant,gross,refunds   (used by the cross-language e2e check)
    Engine.merchantNet(lines).toVector.sortBy(_._1).foreach { case (m, (g, r)) => println(s"$m,$g,$r") }
  else println(Engine.toReport(lines))
  if bad.nonEmpty then sys.exit(1)
