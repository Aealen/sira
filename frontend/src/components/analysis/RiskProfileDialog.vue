<script setup lang="ts">
/** 标的分析·风险偏好设置弹窗：回撤容忍滑杆 / 期限 pill / 目标收益 / 风险态度 radio。
 *  保存调用 PUT /api/profile，成功后 emit('saved') 由父级刷新参考仓位。 */
import { computed, reactive, ref, watch } from 'vue'
import {
  ElButton,
  ElDialog,
  ElInputNumber,
  ElMessage,
  ElRadioButton,
  ElRadioGroup,
  ElSlider,
} from 'element-plus'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/icon/style/css'
import 'element-plus/es/components/slider/style/css'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message/style/css'
import { ATTITUDE_OPTIONS, HORIZON_OPTIONS, profileApi, type RiskProfile } from '../../api/analysis'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'saved'): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const form = reactive<RiskProfile>({
  max_drawdown_tolerance: 15,
  horizon: 'mid',
  target_return: 8,
  attitude: 'balanced',
})
const saving = ref(false)

/** 打开时拉取当前画像（后端未就绪时 analysis.ts 内部回退 mock 默认值） */
watch(visible, async (v) => {
  if (!v) return
  try {
    Object.assign(form, await profileApi.get())
  } catch {
    /* 保留本地默认值 */
  }
})

async function save() {
  saving.value = true
  try {
    await profileApi.update({ ...form })
    ElMessage.success('风险偏好已保存')
    visible.value = false
    emit('saved')
  } catch {
    ElMessage.error('保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="调整风险偏好" width="460px" :close-on-click-modal="false">
    <div class="field">
      <div class="field-head">
        <span>可承受最大回撤</span>
        <span class="field-value">{{ form.max_drawdown_tolerance }}%</span>
      </div>
      <el-slider
        v-model="form.max_drawdown_tolerance"
        :min="5"
        :max="50"
        :step="5"
        :marks="{ 5: '5%', 50: '50%' }"
      />
    </div>

    <div class="field">
      <div class="field-head"><span>投资期限</span></div>
      <div class="pill-group">
        <button
          v-for="o in HORIZON_OPTIONS"
          :key="o.value"
          type="button"
          class="pill"
          :class="{ active: form.horizon === o.value }"
          @click="form.horizon = o.value"
        >
          {{ o.label }}
        </button>
      </div>
    </div>

    <div class="field">
      <div class="field-head"><span>目标年化收益</span></div>
      <div class="target-row">
        <el-input-number v-model="form.target_return" :min="0" :max="50" :step="0.5" :controls="false" />
        <span class="target-unit">%</span>
      </div>
    </div>

    <div class="field">
      <div class="field-head"><span>风险态度</span></div>
      <el-radio-group v-model="form.attitude">
        <el-radio-button v-for="o in ATTITUDE_OPTIONS" :key="o.value" :value="o.value">
          {{ o.label }}
        </el-radio-button>
      </el-radio-group>
    </div>

    <div class="tip">参考仓位上限 = 可承受回撤 ÷ 标的历史最大回撤，保存后自动刷新。</div>

    <template #footer>
      <el-button round @click="visible = false">取消</el-button>
      <el-button type="primary" round :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
:deep(.el-dialog) {
  border-radius: 20px;
}

.field {
  margin-bottom: 22px;
}

.field-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 500;
  margin-bottom: 10px;
}

.field-value {
  color: var(--sira-ink-deep);
  font-weight: 600;
}

/* 滑杆与选中态对齐 Wise 墨色 */
:deep(.el-slider__bar) {
  background: var(--sira-ink);
}
:deep(.el-slider__button) {
  border-color: var(--sira-ink);
}
:deep(.el-slider__marks-text) {
  font-size: 11px;
  color: var(--sira-mute);
}
:deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: var(--sira-ink);
  border-color: var(--sira-ink);
  box-shadow: -1px 0 0 0 var(--sira-ink);
  color: #fff;
}

.pill-group {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pill {
  border: 1px solid var(--sira-canvas-soft);
  background: var(--sira-canvas);
  color: var(--sira-body);
  border-radius: 9999px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}

.pill.active {
  background: var(--sira-ink);
  border-color: var(--sira-ink);
  color: #fff;
  font-weight: 500;
}

.target-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.target-row :deep(.el-input-number) {
  width: 160px;
}

.target-unit {
  color: var(--sira-body);
}

.tip {
  font-size: 12px;
  color: var(--sira-mute);
  background: var(--sira-canvas-soft);
  border-radius: var(--sira-radius-sm);
  padding: 8px 12px;
}
</style>
