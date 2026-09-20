'use strict';

(function installHistoryRateLimitApi(root) {
  const MAX_DISMISS_ATTEMPTS = 2;
  const ZH_TITLE = '请求过于频繁';
  const ZH_HISTORY_TOKENS = ['访问对话记录', '对话记录'];
  const ZH_LIMIT_TOKENS = ['暂时限制', '限制你访问', '限制访问'];
  const DISMISS_LABELS = new Set(['明白了', '知道了', 'Got it', 'OK', 'Okay']);

  function normalizeText(value) {
    return String(value || '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function isHistoryAccessRateLimitText(value) {
    const text = normalizeText(value);
    if (!text) return false;

    const zhHistory = ZH_HISTORY_TOKENS.some(token => text.includes(token));
    const zhLimited = ZH_LIMIT_TOKENS.some(token => text.includes(token));
    if (text.includes(ZH_TITLE) && zhHistory && zhLimited) return true;

    const lower = text.toLowerCase();
    const enTitle = lower.includes('too many requests') || lower.includes('requests too frequent');
    const enHistory = lower.includes('conversation history') || lower.includes('chat history');
    const enLimited = lower.includes('temporarily') && (lower.includes('limit') || lower.includes('restrict'));
    return enTitle && enHistory && enLimited;
  }

  function isDismissLabel(value) {
    return DISMISS_LABELS.has(normalizeText(value));
  }

  const api = Object.freeze({
    MAX_DISMISS_ATTEMPTS,
    normalizeText,
    isHistoryAccessRateLimitText,
    isDismissLabel,
  });

  root.CAHHistoryRateLimit = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
