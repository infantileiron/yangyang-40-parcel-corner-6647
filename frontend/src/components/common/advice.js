import { get } from '../../api/client.js';
import { esc } from '../../utils/html.js';

export async function mountAdviceBanner(root, refresh) {
  const node = root.querySelector('#advice-banner');
  if (!node) return;

  let advice;
  try {
    advice = await get('/recommendation');
  } catch {
    node.hidden = true;
    return;
  }
  if (!node.isConnected) return;

  const renderPending = data => {
    if (!data?.pending_update) return false;
    const runId = Number(data.latest_run_id);
    const target = Number.isSafeInteger(runId) && runId > 0 ? `#result/${runId}` : '#analysis';
    const stage = data.latest?.base_stage || data.current?.base_stage;
    node.hidden = false;
    node.className = `advice-banner${data.update_failed ? ' advice-failed' : ' advice-pending'}`;
    node.innerHTML = `<strong>扩投建议待更新</strong>
      <span>${data.update_failed ? '最新重算未完成，可打开任务查看原因并重跑。' : `跌落点数更正已入账，正在按原条件${stage ? `从最早受影响的第 ${esc(stage)} 阶段` : '从最早受影响阶段'}重算停点。`}</span>
      <a href="${target}">查看重算任务 →</a>`;
    return true;
  };

  if (!renderPending(advice)) {
    node.hidden = true;
    return;
  }

  const timer = setTimeout(async function poll() {
    if (!node.isConnected) return;
    try {
      const next = await get('/recommendation');
      if (!node.isConnected) return;
      if (renderPending(next)) {
        setTimeout(poll, 1200);
      } else {
        refresh?.();
      }
    } catch {
      if (node.isConnected) setTimeout(poll, 2000);
    }
  }, 1200);
  node.dataset.timer = String(timer);
}
