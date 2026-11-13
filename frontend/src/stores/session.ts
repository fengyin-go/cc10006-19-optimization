import { defineStore } from 'pinia'

/** 示例账号目录与后端 seed 的 OWNER_DIRECTORY 对齐。 */
export const PEOPLE = [
  { account: 'zhangwei', name: '张伟', status: '已调岗' },
  { account: 'lina', name: '李娜', status: '在职' },
  { account: 'wangqiang', name: '王强', status: '在职' },
  { account: 'chenjing', name: '陈静', status: '在职' },
  { account: 'zhaolei', name: '赵磊', status: '在职' },
  { account: 'admin', name: '系统管理员', status: '在职' },
]

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: 'lina',
    shiftLabel: '白班 08:00-20:00',
    scope: '数据湖仓一体管理平台',
  }),
  getters: {
    canOperate: (state) => state.operator.length > 0,
    operatorName: (state) => PEOPLE.find((item) => item.account === state.operator)?.name ?? state.operator,
    operatorStatus: (state) => PEOPLE.find((item) => item.account === state.operator)?.status ?? '在职',
    /** 只有管理员身份才带 X-Role，普通值班账号不带。 */
    role: (state) => (state.operator === 'admin' ? 'admin' : ''),
  },
  actions: {
    setOperator(account: string) {
      this.operator = account
    },
    setShift(label: string) {
      this.shiftLabel = label
    },
  },
})
