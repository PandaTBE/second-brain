import test from 'node:test';
import assert from 'node:assert/strict';
import { endpointScope, changeEndpoint, changePrivateNetwork, sameEndpoint, requiresToken, cliPathIssue } from '../static/endpoint-policy.mjs';

test('GigaCode CLI is tokenless but has a separate executable connection identity', () => {
  const cli = { provider: 'gigacode_cli', base_url: 'gigacode://local', cli_path: '/opt/bin/gigacode', allow_private_network: true };
  assert.equal(requiresToken({ id: 'gigacode_cli' }), false);
  assert.equal(requiresToken({ id: 'openai' }), true);
  assert.equal(requiresToken({ id: 'other', requires_token: false }), false);
  assert.equal(endpointScope(cli).cli_path, '/opt/bin/gigacode');
  assert.equal(endpointScope(cli).allow_private_network, false);
  assert.equal(sameEndpoint(cli, { ...cli, cli_path: '/another/gigacode' }), false);
  assert.equal(changeEndpoint(cli, 'openai', 'https://api.example.com/v1').cli_path, '');
  assert.equal(cli.cli_path, '/opt/bin/gigacode');
});

test('CLI executable accepts discovery and absolute paths while rejecting relative and control-character paths', () => {
  assert.equal(cliPathIssue(''), null);
  assert.equal(cliPathIssue('/Applications/Giga Code/bin/gigacode'), null);
  assert.ok(cliPathIssue('gigacode'));
  assert.ok(cliPathIssue('/bin/gigacode\n--debug'));
  assert.ok(cliPathIssue('/bin/gigacode\u0000'));
});

const corporate = { provider: 'custom', base_url: 'https://models.example.internal/api/v2', model_id: 'demo-model', allow_private_network: true };

test('endpoint requests default to public-only and accept only an explicit custom opt-in', () => {
  assert.equal(endpointScope({ provider: 'custom', base_url: corporate.base_url }).allow_private_network, false);
  assert.equal(endpointScope(corporate).allow_private_network, true);
  assert.equal(endpointScope({ ...corporate, allow_private_network: 'true' }).allow_private_network, false);
  assert.equal(endpointScope({ ...corporate, provider: 'openai' }).allow_private_network, false);
  assert.equal(endpointScope({ ...corporate, base_url: ' https://models.example.internal/api/v2/ ' }).base_url, corporate.base_url);
});

test('changing the provider or endpoint revokes private access without mutating the saved draft', () => {
  assert.equal(changeEndpoint(corporate, 'custom', 'https://other.example.internal/v2').allow_private_network, false);
  assert.equal(changeEndpoint(corporate, 'openai', corporate.base_url).allow_private_network, false);
  assert.equal(changeEndpoint(corporate, 'custom', ` ${corporate.base_url}/ `).allow_private_network, true);
  assert.equal(corporate.allow_private_network, true);
  assert.equal(corporate.base_url, 'https://models.example.internal/api/v2');
});

test('the private-access checkbox is strict and cannot grant a named preset access', () => {
  assert.equal(changePrivateNetwork(corporate, false).allow_private_network, false);
  assert.equal(changePrivateNetwork(corporate, 'true').allow_private_network, false);
  assert.equal(changePrivateNetwork({ ...corporate, provider: 'openrouter' }, true).allow_private_network, false);
  assert.equal(changePrivateNetwork({ ...corporate, allow_private_network: false }, true).allow_private_network, true);
  assert.equal(corporate.allow_private_network, true);
});

test('saved-state and async identity include private-access policy while old settings remain equivalent', () => {
  assert.equal(sameEndpoint(corporate, { ...corporate }), true);
  assert.equal(sameEndpoint(corporate, { ...corporate, allow_private_network: false }), false);
  assert.equal(sameEndpoint(corporate, { ...corporate, provider: 'qwen' }), false);
  assert.equal(sameEndpoint({ provider: 'custom', base_url: corporate.base_url }, { ...corporate, allow_private_network: false }), true);
});
