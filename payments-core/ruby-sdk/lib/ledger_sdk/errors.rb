# frozen_string_literal: true

module LedgerSDK
  class Error < StandardError; end

  # The request never produced a usable HTTP response (connection refused, reset, timeout).
  class ConnectionError < Error; end

  class APIError < Error
    attr_reader :status, :body

    def initialize(status, body)
      @status = status
      @body = body
      super("HTTP #{status}: #{body.is_a?(Hash) ? body.dig('error', 'message') || body : body}")
    end
  end

  class InvalidRequestError < APIError; end   # 400
  class NotFoundError < APIError; end         # 404
  class IdempotencyError < APIError; end      # 422: key reused with different parameters
  class ServerError < APIError; end           # 5xx

  class SignatureVerificationError < Error; end

  def self.error_for(status, body)
    klass = case status
            when 400 then InvalidRequestError
            when 404 then NotFoundError
            when 422 then IdempotencyError
            when 500..599 then ServerError
            else APIError
            end
    klass.new(status, body)
  end
end
