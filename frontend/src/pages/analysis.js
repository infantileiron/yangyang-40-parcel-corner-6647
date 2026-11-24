import { get, post } from '../api/client.js';
import { intro } from '../components/common/frame.js';
import { status, table } from '../components/common/data.js';
import { fieldControl, formError } from '../components/common/forms.js';
import { Role } from '../constants/enums.js';
import { bindForm } from '../hooks/actions.js';
import { formValues } from '../types/form.js';
import { esc, timeLabel } from '../utils/html.js';

export function runTable(runs, message) {
  return table([
    {label:'分析名称',html: row => `<a href="#result/${row.id}">${esc(row.title)}</a>`},
    {label:'观测条数',key:'sample_size'},
    {label:'点数版本',html: row => `<span class="revision">v${esc(row.data_revision ?? 0)}</span>${row.is_current ? '<span class="status status-current">现行建议</span>' : row.stale ? '<span class="status status-stale">更正前</span>' : ''}`},
    {label:'状态',html:status},
    {label:'提交时间',html:row => timeLabel(row.created_at)},
  ], runs, message);
}

export async function analysisPage(spec, user) {
  const [runs, advice] = await Promise.all([
    get('/runs'),
    get('/recommendation').catch(() => null),
  ]);
  const adviceRunId = Number(advice?.latest_run_id);
  const adviceHref = Number.isSafeInteger(adviceRunId) && adviceRunId > 0 ? `#result/${adviceRunId}` : '#analysis';
  const adviceBanner = advice?.pending_update ? `<section class="panel advice-banner advice-pending" data-analysis-pending role="status">
      <strong>扩投建议待更新</strong>
      <span>${advice.update_failed ? '最新重算失败，请打开任务查看原因。' : '跌落点数更正已入账，正在按原条件从最早受影响阶段重算停点；追上最新点数前不会恢复为现行建议。'}</span>
      <a href="${adviceHref}">查看重算任务 →</a>
    </section>` : '';
  return `${intro('分析任务', spec.algorithm)}${adviceBanner}<div class="split"><section class="panel"><div class="panel-heading"><h2>计算条件</h2></div>
    ${user.role === Role.ANALYST ? `<form id="run-form" class="panel-body"><label class="field"><span>分析名称</span><input name="title" required maxlength="120" value="${esc(spec.direction)}复核"></label><div class="form-grid">${spec.parameters.map(parameter => fieldControl(parameter,true)).join('')}</div>${formError}<button class="primary">提交计算</button></form>` : '<p class="panel-body">由业务分析员提交计算。此账号可查看当前角色有权访问的分析任务。</p>'}</section>
    <aside class="method"><h2>计算口径</h2><p>${esc(spec.formula)}</p><p class="aside-note">提交时固化参与计算的观测清单与试样快照，后续更正不会改变已排队计算。更正入账后会按原条件自动发起新版本重算；追上最新点数前，页面标明「建议待更新」，旧计算晚完成也不会换回现行建议。</p></aside></div>
    <section class="panel"><div class="panel-heading"><h2>历史分析</h2></div>${runTable(runs, '还没有可查看的分析。提交计算后可在这里追踪。')}</section>`;
}

export function wireAnalysis(root, spec) {
  bindForm(root.querySelector('#run-form'), async form => {
    const run = await post('/runs', {title:form.elements.title.value,parameters:formValues(form,spec.parameters)});
    location.hash = `#result/${run.id}`;
  });
}
