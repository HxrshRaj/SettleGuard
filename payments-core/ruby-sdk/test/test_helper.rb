# frozen_string_literal: true

$LOAD_PATH.unshift File.expand_path("../lib", __dir__)
require "minitest/autorun"
require "socket"
require "json"
require "ledger_sdk"

# Minimal in-process HTTP stub: serves queued [status, body] responses (or :drop to close the
# socket without replying) and records every request it receives.
class StubServer
  attr_reader :requests, :port

  def initialize(responses)
    @responses = responses.dup
    @requests = []
    @server = TCPServer.new("127.0.0.1", 0)
    @port = @server.addr[1]
    @thread = Thread.new { loop_accept }
  end

  def url = "http://127.0.0.1:#{@port}"

  def stop
    @thread.kill
    @server.close
  end

  private

  def loop_accept
    loop do
      sock = @server.accept
      request_line = sock.gets
      headers = {}
      while (line = sock.gets) && line != "\r\n"
        k, v = line.chomp.split(": ", 2)
        headers[k.downcase] = v
      end
      body = sock.read(headers["content-length"].to_i)
      @requests << { line: request_line.strip, headers: headers, body: body }
      resp = @responses.shift || [200, "{}"]
      if resp == :drop
        sock.close
        next
      end
      status, payload = resp
      sock.write("HTTP/1.1 #{status} X\r\nContent-Type: application/json\r\nContent-Length: #{payload.bytesize}\r\nConnection: close\r\n\r\n#{payload}")
      sock.close
    end
  end
end
