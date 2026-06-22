<template>
  <div>
    <div class="node-list">
      <div
        v-for="(node, i) in nodes"
        :key="node.id"
        class="node-item"
        :class="{
          'node-item--dragging': dragIndex === i,
          'node-item--over-top': dropIndex === i && dropPos === 'top',
          'node-item--over-bottom': dropIndex === i && dropPos === 'bottom',
        }"
        @dragover.prevent="onDragOver(i, $event)"
        @dragleave="onDragLeave(i)"
        @drop.prevent="onDrop(i)"
      >
        <span class="node-index">{{ i + 1 }}</span>
        <el-icon
          class="drag-handle"
          :size="18"
          draggable="true"
          @dragstart="onDragStart(i, $event)"
          @dragend="onDragEnd"
          ><Rank
        /></el-icon>
        <el-input
          v-model="node.name"
          :placeholder="$t('botForm.nodeNamePlaceholder')"
          :maxlength="64"
          :class="{ 'is-duplicate': isDuplicate(i) }"
          class="node-input"
        />
        <el-input
          v-model="node.ip"
          placeholder="IP（可选）"
          :maxlength="64"
          class="node-ip-input"
        />
        <span v-if="isDuplicate(i)" class="dup-tip">{{ $t('botForm.duplicateNode') }}</span>
        <el-button
          class="node-remove"
          :icon="Delete"
          text
          type="danger"
          :disabled="nodes.length <= 1"
          @click="removeNode(i)"
        />
      </div>
      <div v-if="!nodes.length || (nodes.length === 1 && !nodes[0].name)" class="node-empty">
        {{ $t('botForm.noNodes') }}
      </div>
    </div>
    <el-button class="add-node-btn" @click="addNode">
      <el-icon><Plus /></el-icon> {{ $t('botForm.addNode') }}
    </el-button>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { Delete, Plus, Rank } from '@element-plus/icons-vue'

let nodeIdSeq = 0

const props = defineProps({
  modelValue: { type: [Object, Array], default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

const nodes = ref(parseInit())
const dragIndex = ref(-1)
const dropIndex = ref(-1)
const dropPos = ref('')

function parseInit() {
  const cfg = props.modelValue
  let entries = [{ name: '', ip: '' }]
  if (cfg) {
    if (Array.isArray(cfg)) {
      // Legacy: ["nodeA", "nodeB"]
      entries = cfg.length ? cfg.map((name) => ({ name, ip: '' })) : [{ name: '', ip: '' }]
    } else if (cfg.clusters) {
      entries = cfg.clusters.map((c) => ({ name: c.name || c.full_name || '', ip: c.ip || '' }))
    } else if (typeof cfg === 'object') {
      // New: {name: ip_str}; or legacy {name: name}
      const keys = Object.keys(cfg)
      if (keys.length) {
        entries = keys.map((name) => {
          const v = cfg[name]
          const ip = typeof v === 'string' ? (v === name ? '' : v) : ''
          return { name, ip }
        })
      }
    }
  }
  return entries.map(({ name, ip }) => ({ id: ++nodeIdSeq, name, ip }))
}

function isDuplicate(i) {
  const val = nodes.value[i]?.name?.trim()
  if (!val) return false
  return nodes.value.some((n, j) => j !== i && n.name?.trim() === val)
}

function addNode() {
  nodes.value.push({ id: ++nodeIdSeq, name: '', ip: '' })
}

function removeNode(i) {
  if (nodes.value.length <= 1) return
  nodes.value.splice(i, 1)
}

function onDragStart(i, e) {
  dragIndex.value = i
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData('text/plain', '')
}

function onDragOver(i, e) {
  if (dragIndex.value === -1 || dragIndex.value === i) return
  const rect = e.currentTarget.getBoundingClientRect()
  const mid = rect.top + rect.height / 2
  dropIndex.value = i
  dropPos.value = e.clientY < mid ? 'top' : 'bottom'
}

function onDragLeave(i) {
  if (dropIndex.value === i) {
    dropIndex.value = -1
    dropPos.value = ''
  }
}

function onDrop(i) {
  const from = dragIndex.value
  if (from === -1 || from === i) return
  const target = dropPos.value === 'bottom' ? (from < i ? i : i + 1) : from < i ? i - 1 : i
  const item = nodes.value.splice(from, 1)[0]
  nodes.value.splice(target, 0, item)
  dragIndex.value = -1
  dropIndex.value = -1
  dropPos.value = ''
}

function onDragEnd() {
  dragIndex.value = -1
  dropIndex.value = -1
  dropPos.value = ''
}

let syncing = false

watch(
  () => props.modelValue,
  (newVal) => {
    if (syncing) return
    syncing = true
    const newNodes = (() => {
      const cfg = newVal
      let entries = [{ name: '', ip: '' }]
      if (cfg) {
        if (Array.isArray(cfg)) {
          entries = cfg.length ? cfg.map((name) => ({ name, ip: '' })) : [{ name: '', ip: '' }]
        } else if (cfg.clusters) {
          entries = cfg.clusters.map((c) => ({
            name: c.name || c.full_name || '',
            ip: c.ip || '',
          }))
        } else if (typeof cfg === 'object') {
          const keys = Object.keys(cfg)
          if (keys.length) {
            entries = keys.map((name) => {
              const v = cfg[name]
              const ip = typeof v === 'string' ? (v === name ? '' : v) : ''
              return { name, ip }
            })
          }
        }
      }
      return entries.map(({ name, ip }) => ({ id: ++nodeIdSeq, name, ip }))
    })()
    nodes.value = newNodes
    nextTick(() => {
      syncing = false
    })
  },
  { deep: true }
)

watch(
  nodes,
  () => {
    const filtered = nodes.value.filter((n) => n.name?.trim())
    syncing = true
    emit(
      'update:modelValue',
      Object.fromEntries(filtered.map((n) => [n.name.trim(), n.ip?.trim() ?? '']))
    )
    nextTick(() => {
      syncing = false
    })
  },
  { deep: true }
)
</script>

<style scoped>
.node-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.node-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--lb-border-light);
  border-radius: 8px;
  background: var(--el-bg-color);
  transition:
    opacity 0.2s,
    box-shadow 0.2s;
  position: relative;
}
.node-item:hover {
  box-shadow: var(--lb-shadow-card-hover, 0 2px 8px rgba(0, 0, 0, 0.06));
}
.node-item--dragging {
  opacity: 0.4;
}
.node-item--over-top::before {
  content: '';
  position: absolute;
  top: -3px;
  left: 8px;
  right: 8px;
  height: 2px;
  border-radius: 1px;
  background: var(--el-color-primary);
}
.node-item--over-bottom::after {
  content: '';
  position: absolute;
  bottom: -3px;
  left: 8px;
  right: 8px;
  height: 2px;
  border-radius: 1px;
  background: var(--el-color-primary);
}
.node-index {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.drag-handle {
  cursor: grab;
  color: var(--el-text-color-placeholder);
  flex-shrink: 0;
  padding: 4px;
  border-radius: 4px;
  transition:
    background-color 0.2s,
    color 0.2s;
}
.drag-handle:hover {
  background-color: var(--el-fill-color-light);
  color: var(--el-text-color-regular);
}
.drag-handle:active {
  cursor: grabbing;
}
.node-input {
  flex: 1;
}
.node-ip-input {
  width: 160px;
  flex-shrink: 0;
}
.node-remove {
  opacity: 0;
  transition: opacity 0.2s;
  flex-shrink: 0;
}
.node-item:hover .node-remove {
  opacity: 1;
}
.node-empty {
  text-align: center;
  padding: 20px;
  color: var(--el-text-color-placeholder);
  font-size: 13px;
  border: 1px dashed var(--lb-border-light);
  border-radius: 8px;
}
.add-node-btn {
  margin-top: 8px;
  border-style: dashed;
}
.is-duplicate :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}
.dup-tip {
  color: var(--el-color-danger);
  font-size: 12px;
  white-space: nowrap;
}
</style>
