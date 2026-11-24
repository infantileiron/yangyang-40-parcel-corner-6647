import { download, get, importFile, post } from '../api/client.js';
import { table } from '../components/common/data.js';
import { intro } from '../components/common/frame.js';
import { fieldControl, formError } from '../components/common/forms.js';
import { Role } from '../constants/enums.js';
import { bindDownloads, bindForm, notify } from '../hooks/actions.js';
import { formValues } from '../types/form.js';
import { esc, timeLabel } from '../utils/html.js';

const COUNT_KEYS = ['old_test', 'old_fail', 'new_test', 'new_fail'];

function newOrderNo() {
  if (globalThis.crypto?.randomUUID) return `CORR-${globalThis.crypto.randomUUID()}`;
  return `CORR-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

function correctionHistory(corrections, fieldLabels) {
  if (!corrections?.length) return '';
  const columns = [
    { key: 'order_no', label: '原单号' },
    { key: 'sheet', label: '批号' },
    { label: '点数项目', html: row => esc(fieldLabels?.[row.field] || row.field || '—') },
    { label: '原点数', html: row => row.original_count ?? '—' },
    { label: '更正后', html: row => row.corrected_count ?? '—' },
    { label: '状态', html: row => row.status === 'applied'
      ? `<span class="status status-approved">已入账${row.replayed ? '·回执重放' : ''}</span>`
      : `<span class="status status-returned">已退回</span>` },
    { label: '重算任务', html: row => row.recompute_run_id ? `<a href="#result/${row.recompute_run_id}">#${row.recompute_run_id}</a>` : '—' },
    { label: '时间', html: row => timeLabel(row.created_at) },
  ];
  return `<section class="panel"><div class="panel-heading"><h2>更正单回执</h2></div>${table(columns, corrections.slice(0, 10), '暂无更正单。')}</section>`;
}

export async function observationPage(spec, user, route) {
  const offset = Math.max(0, Number.parseInt(route.id, 10) || 0);
  const [{total, items}, corrections] = await Promise.all([
    get(`/observations?offset=${offset}&limit=20`),
    get('/corrections').catch(() => []),
  ]);
  const writable = user.role !== Role.VIEWER;
  const countFields = spec.fields.filter(field => COUNT_KEYS.includes(field.key));
  const fieldLabels = Object.fromEntries(spec.fields.map(field => [field.key, field.label]));
  const batchOptions = [...new Set(items.map(item => item.data?.sheet).filter(Boolean))];
  const columns = [
    {key:'id',label:'记录号'},
    ...spec.fields,
    ...(writable ? [{label:'更正', html: row => {
      const values = Object.fromEntries(COUNT_KEYS.map(key => [key, row.data?.[key]]));
      return `<button type="button" class="correct-button" data-correct="${row.id}" data-batch="${esc(row.data?.sheet || '')}" data-values="${esc(JSON.stringify(values))}">更正点数</button>`;
    }}] : []),
  ];
  return `${intro('数据台账', spec.source, `<span class="count">${total} <small>条${esc(spec.record_name)}</small></span>`)}
    ${writable ? `<div class="tools"><details><summary>登记一条${esc(spec.record_name)}</summary><form id="record-form"><div class="form-grid">${spec.fields.map(field => fieldControl(field)).join('')}</div>${formError}<button class="primary">保存记录</button></form></details>
    <details><summary>按模板导入 CSV</summary><form id="import-form"><p>使用 UTF-8 CSV，每次最多 500 条。重复行自动跳过。</p><button type="button" data-download="/observations/template">下载模板</button><label class="field"><span>CSV 文件</span><input type="file" name="file" accept=".csv,text/csv" required></label>${formError}<button class="primary">导入文件</button></form></details></div>
    <section class="panel correction-panel"><div class="panel-heading"><h2>跌落点数更正</h2><span>发货组等待扩投建议时收到的更正，可在此带批号和原点数提交</span></div>
      <form id="correction-form" class="panel-body">
        <div class="form-grid">
          <label class="field"><span>原单号（未收到回执时用此号重试）</span><input name="order_no" required maxlength="191" value="${newOrderNo()}"></label>
          <label class="field"><span>批号</span><input name="sheet" required maxlength="96" list="batch-options" autocomplete="off" placeholder="选择或输入试样批号"></label>
          <label class="field"><span>点数项目</span><select name="field">${countFields.map(field => `<option value="${esc(field.key)}">${esc(field.label)}</option>`).join('')}</select></label>
          <label class="field"><span>原点数</span><input name="original_count" type="number" inputmode="numeric" min="0" max="1000" required></label>
          <label class="field"><span>更正后点数</span><input name="corrected_count" type="number" inputmode="numeric" min="0" max="1000" required></label>
        </div>
        <datalist id="batch-options">${batchOptions.map(sheet => `<option value="${esc(sheet)}"></option>`).join('')}</datalist>
        <p class="form-hint">提交前页面显示的数值即原点数。两人同时更正同一批时，若他人已先入账导致原点数对不上，本单会被退回；请刷新后按新原点数提交。未收到回执时，请保持同一原单号重试，系统不会重复记账。</p>
        ${formError}<button class="primary">提交更正单</button>
      </form>
    </section>` : ''}
    <section class="panel"><div class="panel-heading"><h2>${esc(spec.record_name)}明细</h2>${writable ? '<a class="text-link" href="#analysis">发起分析 →</a>' : ''}</div>
    ${table(columns, items, '暂无观测。分析员可先导入 CSV 或登记样本。')}
    ${total ? `<div class="pagination"><span>第 ${offset + 1}–${Math.min(total,offset + 20)} 条</span>${offset > 0 ? `<a href="#observations/${Math.max(0, offset-20)}">上一页</a>` : ''}${offset+20 < total ? `<a href="#observations/${offset+20}">下一页</a>` : ''}</div>` : ''}</section>
    ${correctionHistory(corrections, fieldLabels)}`;
}

export function wireObservations(root, spec, refresh) {
  bindDownloads(root, download);
  bindForm(root.querySelector('#record-form'), async form => {
    await post('/observations', formValues(form, spec.fields));
    notify('记录已保存');
    if (location.hash === '#observations' || !location.hash) refresh();
    else location.hash = '#observations';
  });
  bindForm(root.querySelector('#import-form'), async form => {
    const report = await importFile(form.elements.file.files[0]);
    notify(`已导入 ${report.added} 条 · 跳过 ${report.skipped} 条重复记录`);
    refresh();
  });

  const correctionForm = root.querySelector('#correction-form');
  const fillOriginal = () => {
    if (!correctionForm) return;
    const button = [...root.querySelectorAll('[data-correct]')]
      .find(item => item.dataset.batch === correctionForm.elements.sheet.value);
    if (!button) return;
    try {
      const values = JSON.parse(button.dataset.values || '{}');
      correctionForm.elements.original_count.value = values[correctionForm.elements.field.value] ?? '';
    } catch {
      // The user can still type the original count manually.
    }
  };
  correctionForm?.elements.sheet.addEventListener('change', fillOriginal);
  correctionForm?.elements.field.addEventListener('change', fillOriginal);
  root.querySelectorAll('[data-correct]').forEach(button => {
    button.addEventListener('click', () => {
      if (!correctionForm) return;
      correctionForm.elements.sheet.value = button.dataset.batch || '';
      correctionForm.elements.field.value = 'old_test';
      let values = {};
      try { values = JSON.parse(button.dataset.values || '{}'); } catch { values = {}; }
      correctionForm.elements.original_count.value = values.old_test ?? '';
      correctionForm.elements.corrected_count.value = '';
      correctionForm.scrollIntoView({behavior: 'smooth', block: 'center'});
      correctionForm.elements.corrected_count.focus();
    });
  });
  bindForm(correctionForm, async form => {
    try {
      const result = await post('/corrections', {
        order_no: form.elements.order_no.value.trim(),
        sheet: form.elements.sheet.value.trim(),
        field: form.elements.field.value,
        original_count: Number(form.elements.original_count.value),
        corrected_count: Number(form.elements.corrected_count.value),
      });
      notify(result.replayed ? '原回执已找到，未重复记账' : '更正已入账，扩投建议待更新');
      refresh();
    } catch (error) {
      // A definitive 409 rejection was received; a corrected business attempt
      // needs a fresh receipt number. Network/5xx unknowns keep the old number
      // so retrying is idempotent.
      if (error.status === 409) form.elements.order_no.value = newOrderNo();
      throw error;
    }
  });
}
