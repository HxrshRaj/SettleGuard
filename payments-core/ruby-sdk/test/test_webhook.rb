# frozen_string_literal: true

require_relative "test_helper"

class WebhookTest < Minitest::Test
  SECRET = "whsec_test_1234"
  PAYLOAD = '{"id":"evt_1"}'
  TS = 1_700_000_000
  # Same vector asserted by the Go test (computed independently with Python's hmac module).
  SIG = "44c25592356f1bebecfd3f032d26e162c59504cb5cdbf7938344b38a924f36a8"

  def header(sig = SIG, ts = TS) = "t=#{ts},v1=#{sig}"

  def test_signature_matches_cross_language_vector
    assert_equal SIG, LedgerSDK::Webhook.compute_signature(SECRET, PAYLOAD, TS)
  end

  def test_valid_event_is_parsed
    evt = LedgerSDK::Webhook.construct_event(PAYLOAD, header, SECRET, now: Time.at(TS + 10))
    assert_equal "evt_1", evt["id"]
  end

  def test_tampered_payload_rejected
    assert_raises(LedgerSDK::SignatureVerificationError) do
      LedgerSDK::Webhook.construct_event('{"id":"evt_2"}', header, SECRET, now: Time.at(TS))
    end
  end

  def test_wrong_secret_rejected
    assert_raises(LedgerSDK::SignatureVerificationError) do
      LedgerSDK::Webhook.construct_event(PAYLOAD, header, "other-secret", now: Time.at(TS))
    end
  end

  def test_stale_timestamp_rejected_replay_protection
    assert_raises(LedgerSDK::SignatureVerificationError) do
      LedgerSDK::Webhook.construct_event(PAYLOAD, header, SECRET, now: Time.at(TS + 301))
    end
  end

  def test_malformed_headers_rejected
    ["", nil, "garbage", "t=abc,v1=#{SIG}", "t=#{TS}", "v1=#{SIG}"].each do |h|
      assert_raises(LedgerSDK::SignatureVerificationError, h.inspect) do
        LedgerSDK::Webhook.construct_event(PAYLOAD, h, SECRET, now: Time.at(TS))
      end
    end
  end

  def test_any_matching_v1_signature_is_accepted_for_secret_rotation
    h = "t=#{TS},v1=#{'0' * 64},v1=#{SIG}"
    assert LedgerSDK::Webhook.construct_event(PAYLOAD, h, SECRET, now: Time.at(TS))
  end

  def test_length_mismatch_does_not_raise_argument_error
    refute LedgerSDK::Webhook.secure_compare("abc", "abcd")
  end
end
