import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import postcss from 'postcss';
import lessSyntax from 'postcss-less';

const stylesheetPath = resolve(process.cwd(), 'src/design/ant/index.less');

async function findRule(selector) {
  const stylesheet = await readFile(stylesheetPath, 'utf8');
  const root = postcss().process(stylesheet, { from: stylesheetPath, syntax: lessSyntax }).root;
  let matchedRule;
  root.walkRules((rule) => {
    if (rule.selectors?.includes(selector)) matchedRule = rule;
  });
  return matchedRule;
}

function declarations(rule) {
  return Object.fromEntries(
    rule.nodes
      .filter((node) => node.type === 'decl')
      .map((node) => [node.prop, `${node.value}${node.important ? ' !important' : ''}`]),
  );
}

test('light-theme table rows remain readable when selected', async () => {
  const selectedRule = await findRule(
    "html[data-theme='light'] .ant-table-wrapper .ant-table-tbody > tr.ant-table-row-selected > td",
  );
  const selectedHoverRule = await findRule(
    "html[data-theme='light'] .ant-table-wrapper .ant-table-tbody > tr.ant-table-row-selected:hover > td",
  );

  assert.ok(selectedRule, 'selected rows must override the generated dark primary background');
  assert.ok(selectedHoverRule, 'selected rows must retain a distinct hover state');
  assert.deepEqual(declarations(selectedRule), {
    background: '#eef4ff !important',
    color: '#1d2939 !important',
  });
  assert.deepEqual(declarations(selectedHoverRule), {
    background: '#e4efff !important',
  });
});
