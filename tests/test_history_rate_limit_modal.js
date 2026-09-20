'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../extension/history_rate_limit.js');

test('matches the observed Chinese conversation-history restriction modal', () => {
  const text = '请求过于频繁 你的请求过于频繁。为保障数据安全，我们已暂时限制你访问对话记录。请稍等几分钟后再重试。';
  assert.equal(api.isHistoryAccessRateLimitText(text), true);
});

test('does not treat a generic request-frequency dialog as history restriction', () => {
  assert.equal(api.isHistoryAccessRateLimitText('请求过于频繁 请稍后重试。'), false);
});

test('does not treat prompt-send rate limiting as history restriction', () => {
  assert.equal(
    api.isHistoryAccessRateLimitText('请求过于频繁 你发送消息过快，请稍后再发送。'),
    false,
  );
});

test('supports equivalent English history-access wording without broad matching', () => {
  assert.equal(
    api.isHistoryAccessRateLimitText(
      'Too many requests. For data security, access to your conversation history is temporarily restricted.'
    ),
    true,
  );
  assert.equal(api.isHistoryAccessRateLimitText('Too many requests. Please retry later.'), false);
});

test('only accepts explicit affirmative dismissal labels', () => {
  assert.equal(api.isDismissLabel('明白了'), true);
  assert.equal(api.isDismissLabel(' Got it '), true);
  assert.equal(api.isDismissLabel('删除'), false);
  assert.equal(api.MAX_DISMISS_ATTEMPTS, 2);
});
