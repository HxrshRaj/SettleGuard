# frozen_string_literal: true

require_relative "test_helper"
require "tmpdir"
require "open3"

# Talks to the real Go server. Skipped unless LEDGERD_BIN points at a built binary.
class IntegrationTest < Minitest::Test
  def setup
    bin = ENV["LEDGERD_BIN"]
    skip "set LEDGERD_BIN to run" unless bin && File.executable?(bin)
    @port = TCPServer.open("127.0.0.1", 0) { |s| s.addr[1] }
    @pid = Process.spawn(bin, "-addr", "127.0.0.1:#{@port}", out: File::NULL, err: File::NULL)
    wait_ready
    @c = LedgerSDK::Client.new(base_url: "http://127.0.0.1:#{@port}")
  end

  def teardown
    return unless @pid

    Process.kill("TERM", @pid)
    Process.wait(@pid)
  end

  def wait_ready
    50.times do
      TCPSocket.new("127.0.0.1", @port).close
      return
    rescue SystemCallError
      sleep 0.1
    end
    flunk "server did not start"
  end

  def test_charge_refund_balance_roundtrip
    ch = @c.create_charge(merchant_id: "m1", amount: 10_000)
    @c.refund_charge(ch["id"], amount: 2_500)
    assert_equal 7_500, @c.balance("m1")["available"]
    assert_equal 2_500, @c.retrieve_charge(ch["id"])["amount_refunded"]
  end

  def test_same_key_is_idempotent_against_real_server
    a = @c.create_charge(merchant_id: "m2", amount: 900, idempotency_key: "dup")
    b = @c.create_charge(merchant_id: "m2", amount: 900, idempotency_key: "dup")
    assert_equal a["id"], b["id"]
    assert_equal 900, @c.balance("m2")["available"]
    assert_raises(LedgerSDK::IdempotencyError) { @c.create_charge(merchant_id: "m2", amount: 1, idempotency_key: "dup") }
  end

  def test_over_refund_and_unknown_charge
    ch = @c.create_charge(merchant_id: "m3", amount: 100)
    assert_raises(LedgerSDK::InvalidRequestError) { @c.refund_charge(ch["id"], amount: 101) }
    assert_raises(LedgerSDK::NotFoundError) { @c.retrieve_charge("ch_nope") }
  end

  def test_concurrent_threads_sharing_one_key_create_one_charge
    keys = Array.new(10) { Thread.new { @c.create_charge(merchant_id: "m4", amount: 50, idempotency_key: "shared")["id"] } }.map(&:value)
    assert_equal 1, keys.uniq.size
    assert_equal 50, @c.balance("m4")["available"]
  end

  def test_events_csv_has_header_and_rows
    @c.create_charge(merchant_id: "m5", amount: 70)
    lines = @c.events_csv.strip.split("\n")
    assert_equal "event_id,type,charge_id,merchant_id,amount,currency,created_at", lines.first
    assert_equal 2, lines.size
  end
end
