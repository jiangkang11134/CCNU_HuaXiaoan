/**
 * 语音输入（Web Speech API）
 *
 * 设计说明：
 * - 纯前端实现，使用浏览器原生 SpeechRecognition / webkitSpeechRecognition，不依赖后端。
 * - 必须运行在「安全上下文」（https 或 localhost）。http 站点在 Chrome 下会直接失败，
 *   这里在 start() 时前置判断并给出明确提示，避免用户以为是 bug。
 * - Chrome/Edge 的识别由云端服务完成，若该服务不可达会抛 network 错误；
 *   这类错误重试无意义，直接停止收音并提示改用键盘输入。
 * - 「连续输入」：浏览器在一段静默后会自动结束会话（触发 onend），
 *   这里在 onend 中自动续接，直到用户主动停止或遇到致命错误。
 */
import { computed, onBeforeUnmount, ref } from 'vue'

// 一次性拿到构造器（Chrome/Edge 为 webkitSpeechRecognition）
const getRecognitionCtor = () => {
  if (typeof window === 'undefined') return null
  return window.SpeechRecognition || window.webkitSpeechRecognition || null
}

// 致命错误：继续重试没有意义，直接停止并提示
const FATAL_ERROR_CODES = new Set([
  'not-allowed',
  'service-not-allowed',
  'audio-capture',
  'language-not-supported',
  'bad-grammar',
  'network'
])

const ERROR_MESSAGES = {
  'not-allowed': '麦克风权限被拒绝，请在浏览器地址栏的权限设置中允许使用麦克风。',
  'service-not-allowed': '浏览器拒绝了语音识别服务，请检查浏览器设置后重试。',
  'audio-capture': '未检测到可用的麦克风设备，请检查设备连接。',
  'language-not-supported': '当前浏览器不支持中文语音识别。',
  'bad-grammar': '语音识别参数有误，已停止收音。',
  network: '语音识别服务连接失败（网络不可达或服务被拦截），请改用键盘输入。',
  'no-speech': '没有听到声音，语音输入已停止。'
}

// 连续多少次「没听到声音」后自动停止
const MAX_NO_SPEECH = 3
// 一次会话允许的自动续接次数上限，防极端情况下无限重启
const MAX_RESTARTS = 60
// onend 与再次 start 之间的间隔，避免 Chrome 引擎未释放导致 InvalidStateError
const RESTART_DELAY = 150

export function useVoiceInput(options = {}) {
  const { lang = 'zh-CN', interimResults = true } = options

  const isSupported = ref(Boolean(getRecognitionCtor()))
  const isListening = ref(false)
  /** 本次会话已定稿的文本 */
  const finalText = ref('')
  /** 尚未定稿的临时文本（会随后续识别结果被替换） */
  const interimText = ref('')
  const errorCode = ref('')
  /** 需要用户知晓的错误文案；由使用方消费后置空 */
  const errorMessage = ref('')

  /** 本次会话累计文本（定稿 + 临时），使用方直接把它写进输入框即可 */
  const transcript = computed(() => finalText.value + interimText.value)

  let recognition = null
  let shouldListen = false // 用户意图：是否处于「收听」状态
  let noSpeechCount = 0
  let restartCount = 0
  let restartTimer = null

  const clearRestartTimer = () => {
    if (restartTimer) {
      clearTimeout(restartTimer)
      restartTimer = null
    }
  }

  const stop = () => {
    shouldListen = false
    isListening.value = false
    interimText.value = ''
    clearRestartTimer()
    if (!recognition) return
    try {
      recognition.stop()
    } catch (err) {
      // 已经停止时再调 stop() 会抛错，属于正常情况
    }
  }

  const buildRecognition = () => {
    const Ctor = getRecognitionCtor()
    if (!Ctor) return null

    const instance = new Ctor()
    instance.lang = lang
    instance.continuous = true
    instance.interimResults = interimResults
    instance.maxAlternatives = 1

    instance.onstart = () => {
      isListening.value = true
      errorCode.value = ''
    }

    instance.onresult = (event) => {
      noSpeechCount = 0
      let interim = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i]
        const text = result[0]?.transcript || ''
        if (result.isFinal) {
          finalText.value += text
        } else {
          interim += text
        }
      }
      interimText.value = interim
    }

    instance.onerror = (event) => {
      const code = event?.error || 'unknown'
      errorCode.value = code

      // 用户主动停止会触发 aborted，不提示
      if (code === 'aborted') return

      if (code === 'no-speech') {
        noSpeechCount += 1
        if (noSpeechCount >= MAX_NO_SPEECH) {
          errorMessage.value = ERROR_MESSAGES['no-speech']
          stop()
        }
        return
      }

      if (FATAL_ERROR_CODES.has(code)) {
        errorMessage.value = ERROR_MESSAGES[code] || '语音识别失败，请重试或改用键盘输入。'
        stop()
      }
    }

    instance.onend = () => {
      interimText.value = ''
      if (!shouldListen) {
        isListening.value = false
        return
      }

      // 浏览器静默后自动断开会走到这里，续接以维持持续输入
      restartCount += 1
      if (restartCount > MAX_RESTARTS) {
        errorMessage.value = '语音输入已持续较长时间，已自动停止。'
        stop()
        return
      }

      clearRestartTimer()
      restartTimer = setTimeout(() => {
        restartTimer = null
        if (!shouldListen || !recognition) return
        try {
          recognition.start()
        } catch (err) {
          shouldListen = false
          isListening.value = false
        }
      }, RESTART_DELAY)
    }

    return instance
  }

  const start = () => {
    if (!isSupported.value) {
      errorMessage.value = '当前浏览器不支持语音输入，建议使用 Chrome 或 Edge。'
      return
    }
    if (typeof window !== 'undefined' && !window.isSecureContext) {
      errorMessage.value = '语音输入需要 HTTPS 环境（或 localhost），当前页面不满足该条件。'
      return
    }
    if (shouldListen) return

    finalText.value = ''
    interimText.value = ''
    errorCode.value = ''
    errorMessage.value = ''
    noSpeechCount = 0
    restartCount = 0
    shouldListen = true

    // 每次重新建实例：复用一个已结束的实例在 Chrome 上偶发无法再次 start
    recognition = buildRecognition()
    if (!recognition) {
      shouldListen = false
      isSupported.value = false
      errorMessage.value = '当前浏览器不支持语音输入，建议使用 Chrome 或 Edge。'
      return
    }

    try {
      recognition.start()
      isListening.value = true
    } catch (err) {
      shouldListen = false
      isListening.value = false
      errorMessage.value = '语音输入启动失败：' + (err?.message || '未知错误')
    }
  }

  const toggle = () => {
    if (isListening.value) stop()
    else start()
  }

  onBeforeUnmount(() => {
    shouldListen = false
    clearRestartTimer()
    if (!recognition) return
    // 先摘掉回调，避免 abort 触发的 onend 再次启动
    recognition.onstart = null
    recognition.onresult = null
    recognition.onerror = null
    recognition.onend = null
    try {
      recognition.abort()
    } catch (err) {
      // 忽略：实例可能已停止
    }
    recognition = null
  })

  return {
    isSupported,
    isListening,
    finalText,
    interimText,
    transcript,
    errorCode,
    errorMessage,
    start,
    stop,
    toggle
  }
}
