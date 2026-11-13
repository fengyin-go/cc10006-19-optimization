<template>
  <section class="page" data-module="audit">
    <header class="page-head">
      <div>
        <h2>操作审计管理</h2>
        <p class="page-desc">维护操作审计日志的输入、执行约束与结果记录，为操作审计提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记审计日志</button>
        <button class="btn" type="button" @click="exportRows">导出操作审计清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>日志编号</span>
        <input v-model="filters.keyword" placeholder="按日志编号检索" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>当前负责人</span>
        <select v-model="filters.owner">
          <option value="">全部</option>
          <option v-for="person in people" :key="person.account" :value="person.account">
            {{ person.name }}（{{ person.account }}）
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>待办范围</span>
        <select v-model="filters.pending">
          <option value="">全部</option>
          <option value="true">仅待办</option>
          <option value="false">仅已完成</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      <button class="btn ghost" type="button" @click="loadTodos">我的待办</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>归属</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ selected: row.id === selectedId }">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span v-if="row['负责人'] === row['原负责人']">{{ row['负责人姓名'] }}</span>
            <span v-else class="tag transferred">
              {{ row['原负责人姓名'] }} → {{ row['负责人姓名'] }}
            </span>
            <span v-if="row['负责人状态'] === '已调岗'" class="tag warn">已调岗</span>
          </td>
          <td class="row-actions">
            <span v-if="isArchived(row)" class="tag readonly">只读</span>
            <button
              v-for="action in allowedActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="selectRow(row)">详情</button>
            <button
              v-if="canTransfer(row)"
              class="link"
              type="button"
              @click="openTransfer(row)"
            >
              变更负责人
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无操作审计数据，可先登记审计日志</td>
        </tr>
      </tbody>
    </table>

    <section v-if="selected" class="detail-panel">
      <header class="page-head">
        <div>
          <h3>审计日志 {{ selected['日志编号'] }} 明细</h3>
          <p class="page-desc">
            原负责人：{{ selected['原负责人姓名'] }} · 当前负责人：{{ selected['负责人姓名'] }}
            <span v-if="selected['负责人状态'] === '已调岗'" class="tag warn">原负责人已调岗</span>
            <span v-if="isArchived(selected)" class="tag readonly">归档只读</span>
          </p>
        </div>
        <button class="btn ghost" type="button" @click="selectedId = null">关闭</button>
      </header>
      <div class="detail-grid">
        <div><span>操作类型</span><br />{{ selected['操作类型'] }}</div>
        <div><span>操作对象</span><br />{{ selected['操作对象'] }}</div>
        <div><span>影响行数</span><br />{{ selected['影响行数'] }}</div>
        <div><span>操作来源</span><br />{{ selected['操作来源'] }}</div>
        <div><span>归属变更时间</span><br />{{ selected['归属变更时间'] ?? '—' }}</div>
        <div><span>归属变更原因</span><br />{{ selected['归属变更原因'] ?? '—' }}</div>
        <div><span>忽略说明</span><br />{{ selected['忽略说明'] ?? '—' }}</div>
        <div><span>回滚能力</span><br />{{ selected['回滚能力'] }}</div>
        <div><span>当前状态</span><br />{{ selected.status }}</div>
      </div>

      <h4>处理历史（责任变更前后均可追溯）</h4>
      <ul class="history-list">
        <li v-for="(item, index) in selected['历史'] ?? []" :key="index">
          {{ item.at }} · {{ item.operatorName }} {{ item.action }}
          <span v-if="item.fromStatus">（{{ item.fromStatus }} → {{ item.toStatus }}）</span>
          <span v-else>（{{ item.toStatus }}）</span>
          · 影响行数 {{ item.impact }}
          <span v-if="item.fromOwner || item.toOwner"> · 归属 {{ item.fromOwner }} → {{ item.toOwner }}</span>
          <span v-if="item.note"> · {{ item.note }}</span>
        </li>
        <li v-if="!(selected['历史'] ?? []).length">暂无历史记录</li>
      </ul>

      <form
        v-if="isArchived(selected)"
        class="note-form"
        @submit.prevent="submitIgnoreNote"
      >
        <input v-model="ignoreNote" placeholder="仅原负责人可补充忽略说明，归档状态不会被改写" />
        <button class="btn" type="submit">补充忽略说明</button>
      </form>
      <p v-if="detailError" class="inline-error">{{ detailError }}</p>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条操作审计记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="createVisible" class="modal-mask" @click.self="createVisible = false">
      <form class="modal-card" @submit.prevent="submitCreate">
        <h3>登记审计日志</h3>
        <label v-for="field in createFields" :key="field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :required="['日志编号', '操作类型', '操作对象'].includes(field)" />
        </label>
        <label>
          <span>影响行数</span>
          <input v-model="createValues['影响行数']" type="number" min="0" />
        </label>
        <label>
          <span>回滚能力</span>
          <select v-model="createValues['回滚能力']">
            <option value="不可回滚">不可回滚</option>
            <option value="可回滚">可回滚</option>
          </select>
        </label>
        <p class="page-desc">操作来源即原负责人：{{ session.operatorName }}（{{ session.operator }}）</p>
        <p v-if="createError" class="inline-error">{{ createError }}</p>
        <div class="row-actions">
          <button class="btn primary" type="submit">确认登记</button>
          <button class="btn ghost" type="button" @click="createVisible = false">取消</button>
        </div>
      </form>
    </div>

    <div v-if="transferTarget" class="modal-mask" @click.self="transferTarget = null">
      <form class="modal-card" @submit.prevent="submitTransfer">
        <h3>变更负责人 {{ transferTarget['日志编号'] }}</h3>
        <p class="page-desc">
          历史操作仍归原负责人 {{ transferTarget['原负责人姓名'] }}，只有未完成待办转给新负责人。
        </p>
        <label>
          <span>新负责人</span>
          <select v-model="transferOwner">
            <option v-for="person in people" :key="person.account" :value="person.account">
              {{ person.name }}（{{ person.account }}）{{ person.status !== '在职' ? '· 不可接收' : '' }}
            </option>
          </select>
        </label>
        <label>
          <span>变更原因</span>
          <input v-model="transferReason" placeholder="如：原负责人调岗" />
        </label>
        <p v-if="transferError" class="inline-error">{{ transferError }}</p>
        <div class="row-actions">
          <button class="btn primary" type="submit">确认转交</button>
          <button class="btn ghost" type="button" @click="transferTarget = null">取消</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson, postJson } from '@/api/client'
import { PEOPLE, useSessionStore } from '@/stores/session'

type Row = Record<string, any>

const ENDPOINT = '/api/audit'
const session = useSessionStore()
const people = PEOPLE
const columns = ["日志编号", "操作类型", "操作对象", "操作来源", "执行时间", "影响行数", "回滚能力"]
const statuses = ["草稿", "待审核", "已归档", "已忽略"]
const createFields = ["日志编号", "操作类型", "操作对象", "操作详情"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref({ keyword: '', status: '', owner: '', pending: '' })
const stats = ref<Record<string, number>>({ total: 0, archived: 0, pending: 0, qualityAlerts: 0 })

const selectedId = ref<number | null>(null)
const selected = ref<Row | null>(null)
const detailError = ref('')
const ignoreNote = ref('')

const createVisible = ref(false)
const createValues = ref<Record<string, string>>({})
const createError = ref('')

const transferTarget = ref<Row | null>(null)
const transferOwner = ref('')
const transferReason = ref('')
const transferError = ref('')

const statCards = computed(() => [
  { label: '审计日志总数', value: stats.value.total ?? 0 },
  { label: '已归档日志', value: stats.value.archived ?? 0 },
  { label: '待处理日志', value: stats.value.pending ?? 0 },
  { label: '质量告警', value: stats.value.qualityAlerts ?? 0 },
  { label: '待处理影响行数', value: stats.value.impactRows ?? 0 },
])

function isArchived(row: Row) {
  return row.status === '已归档' || row.status === '已忽略'
}

/** 前端只做按钮可见性提示，最终权限仍由后端裁决。 */
function allowedActions(row: Row) {
  if (isArchived(row)) return []
  const original = row['原负责人']
  const current = row['负责人']
  const me = session.operator
  const isAdmin = session.role === 'admin'
  const actions: string[] = []
  if (row.status === '草稿' && (me === current || isAdmin)) actions.push('提交审核')
  if (row.status === '待审核' && (me === current || isAdmin)) actions.push('归档日志')
  if ((row.status === '草稿' || row.status === '待审核') && me === original) {
    actions.push('忽略日志')
    if (row['回滚能力'] === '可回滚') actions.push('回滚操作')
  }
  return actions
}

function canTransfer(row: Row) {
  if (isArchived(row)) return false
  return session.role === 'admin' || session.operator === row['原负责人']
}

function resetFilters() {
  filters.value = { keyword: '', status: '', owner: '', pending: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  createValues.value = { '回滚能力': '不可回滚', '影响行数': '0' }
  createError.value = ''
  createVisible.value = true
}

async function submitCreate() {
  createError.value = ''
  try {
    const result = await postJson<any>(ENDPOINT, { values: { ...createValues.value } })
    if (!result.ok) {
      createError.value = result.message
      return
    }
    createVisible.value = false
    await reload()
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '登记失败'
  }
}

function openTransfer(row: Row) {
  transferTarget.value = row
  transferOwner.value = ''
  transferReason.value = ''
  transferError.value = ''
}

async function submitTransfer() {
  if (!transferTarget.value) return
  transferError.value = ''
  try {
    await postJson(`${ENDPOINT}/${transferTarget.value.id}/transfer`, {
      values: { owner: transferOwner.value, reason: transferReason.value },
    })
    transferTarget.value = null
    await reload()
    await refreshStats()
    if (selectedId.value !== null) await selectRowById(selectedId.value)
  } catch (error) {
    transferError.value = error instanceof Error ? error.message : '转交失败'
  }
}

async function selectRow(row: Row) {
  selectedId.value = Number(row.id)
  await selectRowById(selectedId.value)
}

async function selectRowById(id: number) {
  detailError.value = ''
  try {
    selected.value = await fetchJson<Row>(`${ENDPOINT}/${id}`)
  } catch (error) {
    selected.value = null
    detailError.value = error instanceof Error ? error.message : '明细读取失败'
  }
}

async function submitIgnoreNote() {
  if (!selected.value) return
  detailError.value = ''
  try {
    const result = await postJson<any>(`${ENDPOINT}/${selected.value.id}/ignore-note`, {
      values: { note: ignoreNote.value },
    })
    ignoreNote.value = ''
    await selectRowById(Number(selected.value.id))
    await reload()
    await refreshStats()
    if (!result.ok) detailError.value = result.message
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '补充说明失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    await postJson(`${ENDPOINT}/${row.id}/actions`, { values: { action } })
    await reload()
    await refreshStats()
    if (selectedId.value === Number(row.id)) await selectRowById(Number(row.id))
  } catch (error) {
    // 越权 / 只读 / 非法流转都会以 4xx 返回，这里展示后端给的具体原因。
    errorMessage.value = error instanceof Error ? error.message : '操作审计操作失败'
  }
}

async function refreshStats() {
  try {
    stats.value = await fetchJson<Record<string, number>>(`${ENDPOINT}/stats`)
  } catch {
    // 统计失败不覆盖列表结果，页面仍有列表可看。
  }
}

async function loadTodos() {
  filters.value = { ...filters.value, owner: session.operator, pending: 'true' }
  await reload()
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(
    Object.entries(filters.value).filter(([, value]) => value !== '') as [string, string][],
  ).toString()
  try {
    const payload = await fetchJson<{ items: Row[]; total: number }>(`${ENDPOINT}?${query}`)
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作审计列表读取失败'
  }
}

onMounted(async () => {
  await Promise.all([reload(), refreshStats()])
})
</script>
