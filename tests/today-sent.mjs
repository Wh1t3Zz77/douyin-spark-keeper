import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import vm from 'node:vm';
const moduleObject = { exports: {} };
vm.runInNewContext(
  ts.transpileModule(fs.readFileSync('lib/today-sent.ts', 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText,
  { exports: moduleObject.exports, Set, Map },
);
const { todaySentKeys } = moduleObject.exports;
const rec = (id, name) => ({ contact_id: id, contact_name: name });
const run = (task, items) => ({ task_id: task, items });
const submitted = (id, name) => ({ ...rec(id, name), status: 'submitted' });
const tasks = [
  {
    id: 'a',
    recipients: Array.from({ length: 10 }, (_, i) =>
      rec('c' + i, 'friend' + i),
    ),
  },
];
const legacy = run(
  'a',
  Array.from({ length: 9 }, (_, i) => submitted(null, 'friend' + i)),
);
assert.equal(todaySentKeys(tasks, [legacy], []).size, 9);
assert.equal(
  todaySentKeys(tasks, [run('a', [submitted('c0', 'old name')])], []).has(
    'a:c0',
  ),
  true,
);
assert.equal(
  todaySentKeys(
    tasks,
    [run('a', [submitted('different-id', 'friend0')])],
    [],
  ).has('a:c0'),
  false,
);
assert.equal(
  todaySentKeys(tasks, [run('other', [submitted(null, 'friend0')])], []).size,
  0,
);
const duplicate = [
  { id: 'a', recipients: [rec('one', 'same'), rec('two', 'same')] },
];
assert.equal(
  todaySentKeys(duplicate, [run('a', [submitted(null, 'same')])], []).size,
  0,
);
assert.equal(
  todaySentKeys(duplicate, [run('a', [submitted('one', 'same')])], []).has(
    'a:one',
  ),
  true,
);
const failed = run('a', [{ ...rec('c0', 'friend0'), status: 'failed' }]);
assert.equal(todaySentKeys(tasks, [failed, legacy], []).size, 9);
assert.equal(todaySentKeys(tasks, [legacy, failed], []).size, 9);
assert.equal(todaySentKeys(tasks, [legacy, legacy], []).size, 9);
assert.equal(
  todaySentKeys(
    tasks,
    [run('a', [submitted(null, 'new remark')])],
    [{ id: 'c0', display_name: 'new remark' }],
  ).has('a:c0'),
  true,
);
assert.equal(
  todaySentKeys(
    [{ id: 'a', recipients: [rec(null, 'old')] }],
    [run('a', [submitted(null, 'old')])],
    [],
  ).has('a:old'),
  true,
);
console.log('11 summary regression checks passed');
