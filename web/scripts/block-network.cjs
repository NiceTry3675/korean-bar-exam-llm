'use strict'

const http = require('node:http')
const http2 = require('node:http2')
const https = require('node:https')
const childProcess = require('node:child_process')
const dgram = require('node:dgram')
const dns = require('node:dns')
const net = require('node:net')
const tls = require('node:tls')

function blocked() {
  throw new Error('Network access is blocked during tests.')
}

function isDnsNetworkMethod(name) {
  return name === 'lookup'
    || name === 'lookupService'
    || name.startsWith('resolve')
    || name === 'reverse'
}

function blockResolverPrototype(Resolver) {
  if (!Resolver?.prototype) return
  for (const name of Object.getOwnPropertyNames(Resolver.prototype)) {
    if (isDnsNetworkMethod(name)) {
      Resolver.prototype[name] = blocked
    }
  }
}

net.connect = blocked
net.createConnection = blocked
net.Socket.prototype.connect = blocked
tls.connect = blocked
tls.TLSSocket.prototype.connect = blocked
http.request = blocked
http.get = blocked
https.request = blocked
https.get = blocked
http2.connect = blocked
dgram.Socket.prototype.connect = blocked
dgram.Socket.prototype.send = blocked
for (const name of Object.keys(dns)) {
  if (typeof dns[name] === 'function' && isDnsNetworkMethod(name)) {
    dns[name] = blocked
  }
}
blockResolverPrototype(dns.Resolver)
if (dns.promises) {
  for (const name of Object.keys(dns.promises)) {
    if (typeof dns.promises[name] === 'function' && isDnsNetworkMethod(name)) {
      dns.promises[name] = blocked
    }
  }
  blockResolverPrototype(dns.promises.Resolver)
}
// The parent `node --test` process must spawn its workers; block subprocesses
// inside each test worker instead.
if (process.env.NODE_TEST_CONTEXT) {
  childProcess.exec = blocked
  childProcess.execFileSync = blocked
  childProcess.execSync = blocked
  childProcess.execFile = blocked
  childProcess.fork = blocked
  childProcess.spawn = blocked
  childProcess.spawnSync = blocked
}
globalThis.fetch = blocked
if (globalThis.WebSocket) {
  globalThis.WebSocket = class BlockedWebSocket {
    constructor() {
      blocked()
    }
  }
}
