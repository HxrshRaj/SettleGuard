# frozen_string_literal: true

require_relative "test_helper"

class ClientTest < Minitest::Test
  def setup
    @sleeps = []
    @servers = []
  end

  def teardown = @servers.each(&:stop)

  def client_for(responses, **opts)
    s = StubServer.new(responses)
    @servers << s
    [LedgerSDK::Client.new(base_url: s.url, sleeper: ->(d) { @sleeps << d }, **opts), s]
  end

  def test_create_charge_sends_json_and_idempotency_key
    c, s = client_for([[201, '{"id":"ch_1","amount":500}']])
    out = c.create_charge(merchant_id: "m1", amount: 500, idempotency_key: "k-1")
    assert_equal "ch_1", out["id"]
    req = s.requests.first
    assert_equal "POST /v1/charges HTTP/1.1", req[:line]
    assert_equal "k-1", req[:headers]["idempotency-key"]
    assert_equal({ "merchant_id" => "m1", "amount" => 500, "currency" => "INR" }, JSON.parse(req[:body]))
  end

  def test_retries_5xx_reusing_the_same_idempotency_key
    c, s = client_for([[503, "{}"], [502, "{}"], [201, '{"id":"ch_9"}']])
    assert_equal "ch_9", c.create_charge(merchant_id: "m1", amount: 1)["id"]
    keys = s.requests.map { |r| r[:headers]["idempotency-key"] }
    assert_equal 3, keys.size
    assert_equal 1, keys.uniq.size, "every retry must reuse the original key"
    assert_equal [0.2, 0.4], @sleeps
  end

  def test_retries_after_dropped_connection
    c, s = client_for([:drop, [201, '{"id":"ch_2"}']])
    assert_equal "ch_2", c.create_charge(merchant_id: "m1", amount: 1)["id"]
    assert_equal 2, s.requests.size
  end

  def test_gives_up_after_max_retries_and_raises_typed_error
    c, s = client_for([[500, '{"error":{"message":"boom"}}']] * 5, max_retries: 2)
    err = assert_raises(LedgerSDK::ServerError) { c.create_charge(merchant_id: "m1", amount: 1) }
    assert_equal 500, err.status
    assert_equal 3, s.requests.size
  end

  def test_4xx_is_not_retried_and_maps_to_error_classes
    { 400 => LedgerSDK::InvalidRequestError, 404 => LedgerSDK::NotFoundError, 422 => LedgerSDK::IdempotencyError }.each do |code, klass|
      c, s = client_for([[code, '{"error":{"message":"nope"}}']])
      assert_raises(klass) { c.refund_charge("ch_1", amount: 5) }
      assert_equal 1, s.requests.size
    end
  end

  def test_connection_refused_raises_connection_error
    c = LedgerSDK::Client.new(base_url: "http://127.0.0.1:1", max_retries: 1, sleeper: ->(_) {})
    assert_raises(LedgerSDK::ConnectionError) { c.balance("m1") }
  end

  def test_path_segments_are_escaped
    c, s = client_for([[200, '{"available":0}']])
    c.balance("a b/c")
    assert_equal "GET /v1/balances/a+b%2Fc HTTP/1.1", s.requests.first[:line]
  end

  def test_non_json_error_body_does_not_crash
    c, = client_for([[404, "<html>nope</html>"]])
    err = assert_raises(LedgerSDK::NotFoundError) { c.retrieve_charge("x") }
    assert_equal({ "raw" => "<html>nope</html>" }, err.body)
  end
end
