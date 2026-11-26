// 验收脚本和「学生用 JS 写的代码」之间的桥。
//
// 协议只有一行进、一行出：
//   stdin : {"file": "...", "func": "parseReact", "args": [...]}
//   stdout: {"ok": true,  "result": ...}
//         | {"ok": false, "error": "..."}
//
// 和 Python 那边是同一个约定：**最后一行**是 JSON，中间学生自己 print 的东西
// 原样放过。为什么要起进程而不是在验收进程里跑：学生代码会死循环、会
// process.exit()，跑在验收进程里会把它一起带走。
//
// 用动态 import 而不是 require：学生可能写 `export function`（ESM），也可能
// 写 `module.exports = {...}`（CJS），动态 import 两种都能加载。

const fs = require('fs');
const { pathToFileURL } = require('url');

function emit(payload) {
  process.stdout.write(JSON.stringify(payload) + '\n');
}

async function main() {
  const raw = fs.readFileSync(0, 'utf8');
  let request;
  try {
    request = JSON.parse(raw);
  } catch (err) {
    emit({ ok: false, error: '验收桥收到了不合法的请求：' + err.message });
    return;
  }

  let module;
  try {
    module = await import(pathToFileURL(request.file).href);
  } catch (err) {
    // 语法错误、顶层抛异常：把 message 交回去。前缀由验收侧加（它知道这是
    // 「加载失败」而不是「你的函数抛了」），这里只给原始那句。
    emit({ ok: false, error: String((err && err.message) || err) });
    return;
  }

  // 不带 func 就是「只加载，报一下导出了什么」。验收脚本加载时会先问一次：
  // 语法错误、没有 node 这类失败在这里就说清，不用等每条检查各报一遍。
  if (!request.func) {
    const names = new Set();
    for (const key of Object.keys(module)) {
      if (key !== 'default') names.add(key);
    }
    if (module.default && typeof module.default === 'object') {
      for (const key of Object.keys(module.default)) names.add(key);
    }
    emit({ ok: true, exports: [...names] });
    return;
  }

  // 验收要求里写的是 parse_react 这种 snake_case（全课程统一），而 JS 的习惯是
  // parseReact。两个都认：名字不该成为「代码写对了却判不过」的原因。
  const camel = request.func.replace(/_([a-z])/g, (_all, ch) => ch.toUpperCase());
  const pick = (target) => {
    if (!target) return undefined;
    return target[request.func] !== undefined ? target[request.func] : target[camel];
  };
  let fn = pick(module);
  if (typeof fn !== 'function') fn = pick(module.default);
  if (typeof fn !== 'function') {
    emit({
      ok: false,
      error: '模块里没有导出 ' + request.func + '。JS 里要写成 '
        + 'module.exports = { ' + request.func + ': ... }（或 export function ' + camel + '）',
    });
    return;
  }

  let value;
  try {
    value = await fn(...(request.args || []));
  } catch (err) {
    emit({ ok: false, error: String((err && err.message) || err) });
    return;
  }

  // JSON 装不下的返回值（undefined、函数、循环引用）要明确说，不能悄悄变成 null
  if (value === undefined) {
    emit({ ok: true, result: null, undefined: true });
    return;
  }
  let text;
  try {
    text = JSON.stringify(value);
  } catch (err) {
    emit({ ok: false, error: '返回值没法转成 JSON：' + ((err && err.message) || err) });
    return;
  }
  if (text === undefined) {
    emit({ ok: true, result: null, undefined: true });
    return;
  }
  emit({ ok: true, result: JSON.parse(text) });
}

main().catch((err) => {
  emit({ ok: false, error: '验收桥自己出错了：' + ((err && err.message) || err) });
});
