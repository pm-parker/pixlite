<script setup>
/* 诊断报告弹窗: 失败后一键查看全部错误上下文 + 复制转发 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { buildDebugReport, trackError } from '../lib.js'

const show = defineModel({ type: Boolean, default: false })
const report = ref('')
const loading = ref(false)

async function open_() {
  loading.value = true
  try {
    report.value = await buildDebugReport()
  } catch (e) {
    trackError('诊断生成失败: ' + e.message, 'diag')
    report.value = '诊断生成失败: ' + e.message
  } finally { loading.value = false }
}
async function copy() {
  try {
    await navigator.clipboard.writeText(report.value)
    ElMessage.success('诊断报告已复制, 可直接粘贴转发')
  } catch (e) {
    // 非 https fallback
    const ta = document.createElement('textarea')
    ta.value = report.value; document.body.appendChild(ta); ta.select()
    document.execCommand('copy'); ta.remove()
    ElMessage.success('已复制')
  }
}
</script>
<template>
  <el-dialog v-model="show" title="诊断报告" width="640px" @open="open_">
    <div v-loading="loading" style="min-height:120px">
      <el-input type="textarea" :model-value="report" :rows="16" readonly
                style="font-family:monospace;font-size:12px" />
    </div>
    <template #footer>
      <el-button @click="show=false">关闭</el-button>
      <el-button type="primary" @click="copy">复制全部 (转发给开发者)</el-button>
    </template>
  </el-dialog>
</template>
