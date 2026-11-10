export function requiresToken(provider) {
  return provider?.requires_token ?? !['ollama', 'gigacode_cli'].includes(provider?.id);
}

export function cliPathIssue(path = '') {
  return path && (!path.startsWith('/') || /[\u0000\r\n]/.test(path))
    ? 'Укажите абсолютный путь к GigaCode CLI без аргументов команды.' : null;
}

/** Only a deliberate custom-endpoint choice can grant internal network access. */
export function endpointScope(draft) {
  return {
    provider: draft.provider,
    base_url: (draft.base_url || '').trim().replace(/\/+$/, ''),
    allow_private_network: draft.provider === 'custom' && draft.allow_private_network === true,
    ...(draft.provider === 'gigacode_cli' ? { cli_path: (draft.cli_path || '').trim() } : {}),
  };
}

export function sameEndpoint(left, right) {
  return JSON.stringify(endpointScope(left)) === JSON.stringify(endpointScope(right));
}

export function changeEndpoint(draft, provider, baseUrl) {
  const next = { ...draft, provider, base_url: baseUrl, cli_path: provider === 'gigacode_cli' && draft.provider === provider ? draft.cli_path || '' : '' };
  return {
    ...next,
    allow_private_network: endpointScope(draft).allow_private_network
      && draft.provider === provider
      && endpointScope(draft).base_url === endpointScope(next).base_url,
  };
}

export function changePrivateNetwork(draft, enabled) {
  return { ...draft, allow_private_network: draft.provider === 'custom' && enabled === true };
}
