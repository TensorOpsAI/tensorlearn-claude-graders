import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const code = await import(pathToFileURL('/workspace/src/customer-code.mjs'));
const {createClaimsClient} = await import(pathToFileURL('/workspace/src/claims-client.mjs'));
const task = process.argv[2];
assert(['format', 'status'].includes(task));
for (const [input, expected] of [[' cus-000123 ', 'CUS-000123'], ['CUS-000009', 'CUS-000009'], ['cus-890001', 'CUS-890001']]) {
  assert.equal(code.normalizeCustomerCode(input), expected);
}
for (const input of ['CUST-1234', 'CUS-12345', 'CUS-1234567', 'CUS-0000ab', '', null, 123]) {
  assert.throws(() => code.normalizeCustomerCode(input), TypeError);
}
let time = 0, calls = 0;
const client = createClaimsClient(async customerCode => ({customerCode, status: 'pending', version: ++calls}), {now: () => time});
const first = await client.get(' cus-000123 ');
time = 29999;
assert.deepEqual(await client.get('CUS-000123'), first);
assert.equal(calls, 1);
time = 30000;
await client.get('CUS-000123');
assert.equal(calls, 2);
await client.get('CUS-000124');
assert.equal(calls, 3);
let attempts = 0;
const flaky = createClaimsClient(async () => { if (++attempts === 1) throw new Error('temporary'); return {status: 'ready'}; });
await assert.rejects(flaky.get('CUS-000009'));
assert.equal((await flaky.get('CUS-000009')).status, 'ready');
assert.equal(attempts, 2);
if (task === 'format') {
  assert.equal(typeof code.isCustomerCode, 'function', 'Fresh task must add the requested helper');
  for (const input of ['CUS-000123', ' cus-890001 ']) assert.equal(code.isCustomerCode(input), true);
  for (const input of ['CUST-1234', 'CUS-12345', '', null, 123]) assert.equal(code.isCustomerCode(input), false);
} else {
  assert.equal(typeof client.getStatusLabel, 'function', 'Fresh task must add the requested API');
  assert.equal(await client.getStatusLabel('CUS-000124'), 'pending');
  assert.equal(calls, 3, 'Status label must use the existing cached lookup');
  time = 60000;
  assert.equal(await client.getStatusLabel('CUS-000124'), 'pending');
  assert.equal(calls, 4);
}
console.log(JSON.stringify({task, customerCodeContract: true, cacheContract: true, freshTask: true}));
