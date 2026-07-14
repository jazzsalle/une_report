<template>
  <div class="toc-view">
    <div class="toc-toolbar">
      <span class="toc-title">목차 {{ title ? `— ${title}` : '' }}</span>
      <span class="topbar-spacer"></span>
      <button type="button" class="ghost-btn" :disabled="busy" @click="$emit('regenerate')">
        목차 재요청
      </button>
      <button type="button" :disabled="busy || sections.length === 0" @click="$emit('generate-content')">
        본문 생성
      </button>
    </div>
    <p class="toc-help">
      항목을 클릭해 수정하고, ▲▼로 순서를 바꾸거나 +로 하위 목차를 추가하세요.
      편집이 끝나면 [본문 생성]을 누르세요.
    </p>
    <ul class="toc-tree">
      <TocNodeList :nodes="sections" :busy="busy" @change="onChange" />
    </ul>
    <button type="button" class="link-btn toc-add-root" :disabled="busy" @click="addRoot">
      + 최상위 목차 추가
    </button>
  </div>
</template>

<script setup>
// 재귀 목차 편집 뷰 (지시 6: 조회·수정·추가/삭제·순서 변경)
import { defineComponent, h } from 'vue'

const props = defineProps({
  title: { type: String, default: '' },
  sections: { type: Array, required: true }, // [{name, children[]}] — 부모 소유, in-place 편집
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['regenerate', 'generate-content', 'change'])

function onChange() {
  emit('change')
}

function addRoot() {
  props.sections.push({ name: '새 목차', children: [] })
  onChange()
}

/** 재귀 노드 목록 — 렌더 함수 컴포넌트 (단일 파일 내 재귀) */
const TocNodeList = defineComponent({
  name: 'TocNodeList',
  props: {
    nodes: { type: Array, required: true },
    busy: { type: Boolean, default: false },
  },
  emits: ['change'],
  setup(p, { emit: e }) {
    const changed = () => e('change')

    const move = (idx, delta) => {
      const j = idx + delta
      if (j < 0 || j >= p.nodes.length) return
      const [item] = p.nodes.splice(idx, 1)
      p.nodes.splice(j, 0, item)
      changed()
    }
    const remove = (idx) => {
      p.nodes.splice(idx, 1)
      changed()
    }
    const addChild = (node) => {
      if (!node.children) node.children = []
      node.children.push({ name: '새 하위 목차', children: [] })
      changed()
    }
    const rename = (node, ev) => {
      node.name = ev.target.value
      changed()
    }

    return () =>
      p.nodes.map((node, idx) =>
        h('li', { key: idx, class: 'toc-node' }, [
          h('div', { class: 'toc-row' }, [
            h('input', {
              class: 'toc-name',
              value: node.name,
              disabled: p.busy,
              onChange: (ev) => rename(node, ev),
            }),
            h('span', { class: 'toc-actions' }, [
              h('button', {
                type: 'button', class: 'icon-btn', title: '위로',
                disabled: p.busy || idx === 0,
                onClick: () => move(idx, -1),
              }, '▲'),
              h('button', {
                type: 'button', class: 'icon-btn', title: '아래로',
                disabled: p.busy || idx === p.nodes.length - 1,
                onClick: () => move(idx, 1),
              }, '▼'),
              h('button', {
                type: 'button', class: 'icon-btn', title: '하위 추가',
                disabled: p.busy,
                onClick: () => addChild(node),
              }, '+'),
              h('button', {
                type: 'button', class: 'icon-btn icon-danger', title: '삭제',
                disabled: p.busy,
                onClick: () => remove(idx),
              }, '×'),
            ]),
          ]),
          (node.children && node.children.length)
            ? h('ul', { class: 'toc-children' },
                h(TocNodeList, {
                  nodes: node.children, busy: p.busy, onChange: changed,
                }))
            : null,
        ]),
      )
  },
})
</script>
