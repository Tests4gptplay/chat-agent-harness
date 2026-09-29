async (page) => {
  const targets = __CAH_TARGETS_JSON__;
  const noChatsRe = /(暂无聊天|没有聊天|no chats|no conversations)/i;
  const loadingRe = /(正在加载聊天|loading chats|loading conversations)/i;
  const deleteRe = /(删除|刪除|削除|delete)/i;
  const opsRe = /(聊天操作|的操作|chat actions|chat options|options)/i;
  const showMoreRe = /(展开显示|显示更多|show more|more)/i;

  async function anyVisible(locator) {
    const count = await locator.count();
    for (let i = 0; i < count; i++) {
      try {
        if (await locator.nth(i).isVisible())
          return true;
      } catch {}
    }
    return false;
  }

  async function projectScopedIds(target) {
    const links = page.locator('a[href^="/g/' + target.project_key + '/c/"]:visible');
    return await links.evaluateAll((nodes, key) => {
      const escaped = key.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&');
      const re = new RegExp('^/g/' + escaped + '/c/([A-Za-z0-9-]+)(?:/|$)');
      const seen = new Set();
      const out = [];
      for (const node of nodes) {
        const href = node.getAttribute('href') || '';
        const match = href.match(re);
        if (!match || seen.has(match[1]))
          continue;
        seen.add(match[1]);
        out.push(match[1]);
      }
      return out;
    }, target.project_key);
  }

  async function projectContainer(target) {
    const button = page.getByRole('button', { name: target.display_name, exact: true }).first();
    if (await button.count()) {
      const expanded = await button.getAttribute('aria-expanded');
      if (expanded === 'false') {
        await button.click();
        await page.waitForTimeout(250);
      }
      return button.locator('xpath=ancestor::li[1]');
    }
    return null;
  }

  async function expandProjectList(target) {
    const container = await projectContainer(target);
    if (!container)
      return;
    for (let i = 0; i < 20; i++) {
      const more = container.getByRole('button', { name: showMoreRe }).first();
      if (!(await more.count()) || !(await more.isVisible().catch(() => false)))
        break;
      await more.click();
      await page.waitForTimeout(250);
    }
  }

  async function clearComposer() {
    const composer = page.locator(
      '#prompt-textarea:visible, ' +
      'textarea[data-testid="prompt-textarea"]:visible, ' +
      'div[contenteditable="true"][data-lexical-editor="true"]:visible, ' +
      '[contenteditable="true"][role="textbox"]:visible'
    ).first();
    if (!(await composer.count()))
      return { cleared: false, prior_length: 0 };

    let prior = '';
    try {
      prior = (await composer.getAttribute('contenteditable')) === 'true'
        ? (await composer.innerText())
        : (await composer.inputValue());
    } catch {}
    prior = prior || '';
    if (prior.trim()) {
      await composer.fill('');
      await page.waitForTimeout(150);
      return { cleared: true, prior_length: prior.length };
    }
    return { cleared: false, prior_length: 0 };
  }

  async function projectListState(target) {
    const container = await projectContainer(target);
    let sidebarLoading = false;
    let sidebarEmpty = false;

    if (container) {
      sidebarLoading = await anyVisible(container.getByText(loadingRe));
      sidebarEmpty = await anyVisible(container.getByText(noChatsRe));
    }

    // The main Project chat panel exposes its own "暂无聊天" heading when the
    // selected Project has finished loading with no conversations. Keep this
    // scoped to the current Project main content so unrelated "最近/Recent"
    // loading indicators cannot block reset.
    const main = page.locator('main').last();
    const mainEmpty = await anyVisible(main.getByText(noChatsRe));

    return {
      loading: sidebarLoading,
      empty: sidebarEmpty || mainEmpty
    };
  }

  async function waitProjectLoaded(target, timeoutMs = 45000) {
    const deadline = Date.now() + timeoutMs;
    let stableEmptySince = 0;

    while (Date.now() < deadline) {
      await expandProjectList(target);
      const ids = await projectScopedIds(target);
      if (ids.length)
        return ids;

      const state = await projectListState(target);

      if (!state.loading && state.empty)
        return [];

      if (!state.loading) {
        if (!stableEmptySince)
          stableEmptySince = Date.now();
        if (Date.now() - stableEmptySince >= 2500)
          return [];
      } else {
        stableEmptySince = 0;
      }
      await page.waitForTimeout(250);
    }
    throw new Error('Timed out waiting for project chat list: ' + target.display_name);
  }

  async function deleteConversation(target, conversationId) {
    const hrefPrefix = '/g/' + target.project_key + '/c/' + conversationId;
    const projectMain = page.locator('main').last();

    let exact = projectMain.locator(
      'a[href^="' + hrefPrefix + '"]:visible'
    ).first();

    if (!(await exact.count())) {
      exact = page.locator(
        'a[href^="' + hrefPrefix + '"]:visible'
      ).last();
    }

    await exact.waitFor({ state: 'visible', timeout: 15000 });

    const title = ((await exact.innerText().catch(() => '')) || '').trim();
    await exact.hover();
    await page.waitForTimeout(150);

    const escapeRegex = (value) =>
      value.replace(/[.*+?^$()|[\]{}\\-]/g, '\\$&');

    let ops = page.getByRole('button', {
      name: new RegExp(
        '^' + escapeRegex(title) + '.*(?:操作|actions?|options?)$',
        'i'
      ),
      includeHidden: true
    }).last();

    if (!(await ops.count())) {
      ops = page.getByRole('button', {
        name: /^(?:聊天操作|chat actions?|chat options?|options?)$/i,
        includeHidden: true
      }).last();
    }

    if (!(await ops.count())) {
      throw new Error(
        'Chat operation button missing for ' + conversationId + ' title=' + title
      );
    }

    await ops.click({ force: true });

    let deleteItem = page
      .locator('[data-testid="delete-chat-menu-item"]:visible')
      .first();

    if (!(await deleteItem.count())) {
      deleteItem = page
        .getByRole('menuitem', { name: deleteRe })
        .last();
    }

    if (!(await deleteItem.count())) {
      throw new Error('Delete menu item missing for ' + conversationId);
    }

    await deleteItem.click();

    const dialog = page.getByRole('dialog').last();
    if (
      await dialog.count() &&
      await dialog.isVisible().catch(() => false)
    ) {
      const confirm = dialog
        .getByRole('button', { name: deleteRe })
        .last();

      if (!(await confirm.count())) {
        throw new Error('Delete confirmation missing for ' + conversationId);
      }

      await confirm.click();
    }

    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const count = await page.locator(
        'a[href^="' + hrefPrefix + '"]:visible'
      ).count();

      if (!count)
        return;

      await page.waitForTimeout(250);
    }

    throw new Error('Conversation remained after delete: ' + conversationId);
  }

  const report = {
    ok: true,
    project_count: targets.length,
    deleted_count: 0,
    remaining_count: 0,
    projects: []
  };

  for (const target of targets) {
    await page.goto(target.project_root_url, {
      waitUntil: 'domcontentloaded',
      timeout: 120000
    });

    const heading = page.getByRole('heading', { name: target.display_name, exact: true }).first();
    if (await heading.count())
      await heading.waitFor({ state: 'visible', timeout: 30000 });

    const composer = await clearComposer();
    const beforeIds = await waitProjectLoaded(target);
    let deleted = 0;

    for (let guard = 0; guard < 500; guard++) {
      await expandProjectList(target);
      const ids = await projectScopedIds(target);
      if (!ids.length) {
        const stable = await waitProjectLoaded(target, 5000);
        if (!stable.length)
          break;
        continue;
      }
      await deleteConversation(target, ids[0]);
      deleted += 1;
      report.deleted_count += 1;
      await page.waitForTimeout(200);
    }

    await page.reload({ waitUntil: 'domcontentloaded', timeout: 120000 });
    const remaining = await waitProjectLoaded(target);
    if (remaining.length)
      throw new Error(target.display_name + ' still has conversations: ' + remaining.join(','));

    report.projects.push({
      kind: target.kind,
      name: target.display_name,
      project_key: target.project_key,
      before_count: beforeIds.length,
      deleted_count: deleted,
      remaining_count: 0,
      composer_cleared: composer.cleared,
      composer_prior_length: composer.prior_length
    });
  }

  return report;
}
