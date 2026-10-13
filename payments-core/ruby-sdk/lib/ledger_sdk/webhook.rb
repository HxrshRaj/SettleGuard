# frozen_string_literal: true

require "openssl"
require "json"

module LedgerSDK
  # Verifies the `Ledger-Signature: t=<unix>,v1=<hex>` header produced by the Go server.
  # The signed payload is "<t>.<raw body>" using HMAC-SHA256.
  module Webhook
    DEFAULT_TOLERANCE = 300 # seconds

    module_function

    def compute_signature(secret, payload, timestamp)
      OpenSSL::HMAC.hexdigest("SHA256", secret, "#{timestamp}.#{payload}")
    end

    def construct_event(payload, header, secret, tolerance: DEFAULT_TOLERANCE, now: Time.now)
      timestamp, signatures = parse_header(header)
      raise SignatureVerificationError, "timestamp outside tolerance" if tolerance && (now.to_i - timestamp).abs > tolerance

      expected = compute_signature(secret, payload, timestamp)
      ok = signatures.any? { |s| secure_compare(expected, s) }
      raise SignatureVerificationError, "no matching signature" unless ok

      JSON.parse(payload)
    end

    def parse_header(header)
      raise SignatureVerificationError, "missing signature header" if header.nil? || header.empty?

      parts = header.split(",").map { |kv| kv.split("=", 2) }
      timestamp = parts.find { |k, _| k == "t" }&.last
      signatures = parts.select { |k, _| k == "v1" }.map(&:last)
      raise SignatureVerificationError, "malformed signature header" if timestamp.nil? || signatures.empty? || timestamp !~ /\A\d+\z/

      [timestamp.to_i, signatures]
    end

    # Constant-time comparison; length mismatch short-circuits, which leaks only the (public) length.
    def secure_compare(a, b)
      return false unless a.bytesize == b.bytesize

      OpenSSL.fixed_length_secure_compare(a, b)
    end
  end
end
