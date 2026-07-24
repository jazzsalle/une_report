<template>
  <div class="criteria-panel">
    <div class="panel-head">
      <span class="panel-title">기준정보 입력</span>
      <span class="criteria-hint">* 필수</span>
      <span class="topbar-spacer"></span>
      <button type="button" class="icon-btn" title="패널 접기" @click="$emit('collapse')">«</button>
    </div>
    <div class="criteria-scroll">
      <!-- 문서 주제 (subject) -->
      <label class="field">
        <span class="field-label">문서 주제 (subject) <span class="field-required">*</span></span>
        <input v-model.trim="form.subject" type="text" placeholder="예) 2026년 코로나19 재유행 대비계획" />
      </label>

      <!-- 배경정보 (backgroundInfo) -->
      <fieldset class="field-group">
        <legend>배경정보 (backgroundInfo)</legend>
        <label class="field">
          <span class="field-label">재난유형 (disasterType) <span class="field-required">*</span></span>
          <select v-model="form.disasterType">
            <option value="" disabled>선택</option>
            <option v-for="t in DISASTER_TYPES" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">재난관리단계 (controlPhase) <span class="field-required">*</span></span>
          <select v-model="form.controlPhase">
            <option value="" disabled>선택</option>
            <option v-for="p in CONTROL_PHASES" :key="p" :value="p">{{ p }}</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">발생장소 (location)</span>
          <input v-model.trim="form.location" type="text" placeholder="예) 경기도 수원시" />
        </label>
        <label class="field">
          <span class="field-label">재난 발생일시 (startTime)</span>
          <input v-model="form.startTime" type="datetime-local" />
        </label>
        <label class="field">
          <span class="field-label">재난 종료일시 (endTime)</span>
          <input v-model="form.endTime" type="datetime-local" />
        </label>
        <label class="field">
          <span class="field-label">보고일시 (reportTime)</span>
          <input v-model="form.reportTime" type="datetime-local" />
        </label>
      </fieldset>

      <!-- 내용지침 (contentInstruction) -->
      <fieldset class="field-group">
        <legend>내용지침 (contentInstruction)</legend>
        <label class="field">
          <span class="field-label">필수 포함 요소 (essentialFactors)</span>
          <input v-model.trim="form.essentialFactors" type="text" placeholder="쉼표로 구분 — 예) 법적근거, 최근7일통계" />
        </label>
        <label class="field">
          <span class="field-label">작성 가이드 (writingGuide) — 200자 이내</span>
          <textarea v-model.trim="form.writingGuide" rows="3" maxlength="200"
                    placeholder="예) 명확하고 구체적으로 작성"></textarea>
        </label>
        <label class="field">
          <span class="field-label">출처 표기 (source)</span>
          <select v-model="form.source">
            <option value="">선택안함</option>
            <option value="문장 끝 괄호 표기">문장 끝 괄호 표기</option>
            <option value="주석(줄바꿈)">주석(줄바꿈)</option>
          </select>
        </label>
      </fieldset>

      <!-- 표현규칙 (expressionRule) -->
      <fieldset class="field-group">
        <legend>표현규칙 (expressionRule)</legend>
        <label class="field">
          <span class="field-label">문체 (tone)</span>
          <select v-model="form.tone">
            <option value="">선택안함</option>
            <option value="개조식">개조식</option>
            <option value="서술식">서술식</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">문장길이 제한 (maxSentenceLength)</span>
          <select v-model="form.maxSentenceLength">
            <option value="">선택안함</option>
            <option value="1문장 80자 이내">1문장 80자 이내</option>
            <option value="1문장 110자 이내">1문장 110자 이내</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">문단 개요번호 모양 (paragraphSymbol)</span>
          <select v-model="form.paragraphSymbol">
            <option value="">선택안함</option>
            <option value="□, ○, ―">□, ○, ―</option>
            <option value="1.-1.1-1.1.1">1.-1.1-1.1.1</option>
            <option value="1.-□-○-―">1.-□-○-―</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">본문 문장 시작 (bodytextStart)</span>
          <select v-model="form.bodytextStart">
            <option value="">선택안함</option>
            <option value="키워드 괄호 요약(제목 제외)">키워드 괄호 요약(제목 제외)</option>
          </select>
        </label>
      </fieldset>

      <!-- 문서 작성 목적 (purposeOfDocument) -->
      <fieldset class="field-group">
        <legend>문서 작성 목적 (purposeOfDocument)</legend>
        <label class="field">
          <span class="field-label">업무 목적 (goalOfBusiness) <span class="field-required">*</span></span>
          <select v-model="form.goalOfBusiness">
            <option value="" disabled>선택</option>
            <option value="재난안전계획서 작성">재난안전계획서 작성</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">역할 (role) <span class="field-required">*</span></span>
          <select v-model="form.role">
            <option value="" disabled>선택</option>
            <option value="재난안전계획 수립 담당자">재난안전계획 수립 담당자</option>
            <option value="행정기관 보고서 작성자">행정기관 보고서 작성자</option>
          </select>
        </label>
        <div class="field">
          <span class="field-label">타깃 독자 (targetAudiences, 복수) <span class="field-required">*</span></span>
          <div class="check-row">
            <label v-for="a in AUDIENCES" :key="a" class="check-item">
              <input v-model="form.targetAudiences" type="checkbox" :value="a" /> {{ a }}
            </label>
          </div>
        </div>
      </fieldset>

      <!-- 기준정보 미리보기 (UFR-Prompt-01) -->
      <details class="criteria-preview">
        <summary>기준정보 미리보기</summary>
        <pre>{{ previewJson }}</pre>
      </details>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'

// 요구사항정의서(UFR-Info-02/03 등)의 폼 선택지 그대로
const DISASTER_TYPES = [
  '폭염', '태풍/호우', '지진', '황사', '산불',
  '감염병', '가축질병', '다중밀집건축물붕괴대형사고', '정부주요시설', '학교시설',
]
const CONTROL_PHASES = ['예방', '대비']
const AUDIENCES = ['중앙정부', '지자체', '내부보고', '대민']

const emit = defineEmits(['update:criteria', 'update:missing', 'collapse'])

const form = reactive({
  subject: '',
  disasterType: '',
  controlPhase: '',
  location: '',
  startTime: '',
  endTime: '',
  reportTime: '',
  essentialFactors: '',
  writingGuide: '',
  source: '',
  tone: '',
  maxSentenceLength: '',
  paragraphSymbol: '',
  bodytextStart: '',
  goalOfBusiness: '재난안전계획서 작성',
  role: '',
  targetAudiences: [],
})

/** datetime-local 값 → ISO8601 (초 단위, 명세 date-time 형식) */
function toIso(value) {
  return value ? `${value}:00` : ''
}

/** "선택안함"·빈 값 필드는 키 자체를 생략해 API 기준정보를 만든다 (설계 §2). */
const criteria = computed(() => {
  const data = { subject: form.subject }

  const bg = { disasterType: form.disasterType, controlPhase: form.controlPhase }
  if (form.location) bg.location = form.location
  if (form.startTime) bg.startTime = toIso(form.startTime)
  if (form.endTime) bg.endTime = toIso(form.endTime)
  if (form.reportTime) bg.reportTime = toIso(form.reportTime)
  data.backgroundInfo = bg

  const ci = {}
  const factors = form.essentialFactors.split(',').map((s) => s.trim()).filter(Boolean)
  if (factors.length) ci.essentialFactors = factors
  if (form.writingGuide) ci.writingGuide = form.writingGuide
  if (form.source) ci.source = form.source
  if (Object.keys(ci).length) data.contentInstruction = ci

  const er = {}
  if (form.tone) er.tone = form.tone
  if (form.maxSentenceLength) er.maxSentenceLength = form.maxSentenceLength
  if (form.paragraphSymbol) er.paragraphSymbol = form.paragraphSymbol
  if (form.bodytextStart) er.bodytextStart = form.bodytextStart
  if (Object.keys(er).length) data.expressionRule = er

  data.purposeOfDocument = {
    goalOfBusiness: form.goalOfBusiness,
    role: form.role,
    targetAudiences: [...form.targetAudiences],
  }
  return data
})

/** 미입력 필수 항목의 한국어 라벨 목록 */
const missing = computed(() => {
  const out = []
  if (!form.subject) out.push('문서 주제')
  if (!form.disasterType) out.push('재난유형')
  if (!form.controlPhase) out.push('재난관리단계')
  if (!form.goalOfBusiness) out.push('업무 목적')
  if (!form.role) out.push('역할')
  if (form.targetAudiences.length === 0) out.push('타깃 독자')
  return out
})

const previewJson = computed(() => JSON.stringify(criteria.value, null, 2))

watch(criteria, (v) => emit('update:criteria', v), { immediate: true, deep: true })
watch(missing, (v) => emit('update:missing', v), { immediate: true, deep: true })

/** 부모가 채팅 메시지를 주제로 흡수할 때 사용 (subject가 비어 있을 때) */
function setSubject(text) {
  if (!form.subject) form.subject = text
}
defineExpose({ setSubject })
</script>
