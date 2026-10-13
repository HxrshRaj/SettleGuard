# frozen_string_literal: true

require "net/http"
require "json"
require "securerandom"
require "uri"

module LedgerSDK
  class Client
    RETRYABLE_STATUSES = [429, 500, 502, 503, 504].freeze

    attr_reader :base_url, :max_retries

    def initialize(base_url:, max_retries: 2, retry_base_delay: 0.2, timeout: 5, sleeper: ->(s) { sleep(s) })
      @base_url = URI(base_url)
      @max_retries = max_retries
      @retry_base_delay = retry_base_delay
      @timeout = timeout
      @sleeper = sleeper
    end

    # Creates a charge. The idempotency key is generated once and reused for every retry, so a
    # response lost in transit can never cause a duplicate charge.
    def create_charge(merchant_id:, amount:, currency: "INR", idempotency_key: SecureRandom.uuid)
      request(:post, "/v1/charges", { merchant_id: merchant_id, amount: amount, currency: currency }, idempotency_key)
    end

    def refund_charge(charge_id, amount:, idempotency_key: SecureRandom.uuid)
      request(:post, "/v1/charges/#{URI.encode_www_form_component(charge_id)}/refunds", { amount: amount }, idempotency_key)
    end

    def retrieve_charge(charge_id)
      request(:get, "/v1/charges/#{URI.encode_www_form_component(charge_id)}")
    end

    def balance(merchant_id)
      request(:get, "/v1/balances/#{URI.encode_www_form_component(merchant_id)}")
    end

    def register_webhook_endpoint(url:, secret:)
      request(:post, "/v1/webhook_endpoints", { url: url, secret: secret })
    end

    def events_csv
      request(:get, "/v1/events.csv", raw: true)
    end

    private

    def request(method, path, body = nil, idempotency_key = nil, raw: false)
      attempt = 0
      begin
        attempt += 1
        status, text = perform(method, path, body, idempotency_key)
        if RETRYABLE_STATUSES.include?(status) && attempt <= max_retries && retry_safe?(method, idempotency_key)
          backoff(attempt)
          raise RetryableStatus
        end
        handle(status, text, raw)
      rescue RetryableStatus
        retry
      rescue ConnectionError
        raise unless attempt <= max_retries && retry_safe?(method, idempotency_key)

        backoff(attempt)
        retry
      end
    end

    class RetryableStatus < StandardError; end

    # GETs are naturally idempotent; POSTs only retry when they carry an idempotency key.
    def retry_safe?(method, key)
      method == :get || !key.nil?
    end

    def backoff(attempt)
      @sleeper.call(@retry_base_delay * (2**(attempt - 1)))
    end

    def perform(method, path, body, key)
      uri = base_url.dup
      uri.path = path
      req = (method == :post ? Net::HTTP::Post : Net::HTTP::Get).new(uri)
      req["Content-Type"] = "application/json"
      req["Idempotency-Key"] = key if key
      req.body = JSON.generate(body) if body
      http = Net::HTTP.new(uri.host, uri.port)
      http.open_timeout = http.read_timeout = @timeout
      res = http.request(req)
      [res.code.to_i, res.body.to_s]
    rescue SystemCallError, IOError, Timeout::Error, Net::OpenTimeout, Net::ReadTimeout, EOFError => e
      raise ConnectionError, "#{e.class}: #{e.message}"
    end

    def handle(status, text, raw)
      return text if raw && status.between?(200, 299)

      parsed = text.empty? ? {} : (JSON.parse(text) rescue { "raw" => text })
      raise LedgerSDK.error_for(status, parsed) unless status.between?(200, 299)

      parsed
    end
  end
end
