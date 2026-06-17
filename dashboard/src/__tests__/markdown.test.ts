import { describe, expect, it } from 'vitest'
import { renderMarkdown } from '@/utils/markdown'

describe('renderMarkdown', () => {
  it('renders report markdown into readable html', () => {
    const html = renderMarkdown([
      '# 搜索反馈分析报告',
      '',
      '## 结论摘要',
      '这是 **重点**，涉及 `语音输入`。',
      '',
      '- 识别失败',
      '- 快捷键无效',
    ].join('\n'))

    expect(html).toContain('<h1>搜索反馈分析报告</h1>')
    expect(html).toContain('<h2>结论摘要</h2>')
    expect(html).toContain('<strong>重点</strong>')
    expect(html).toContain('<code>语音输入</code>')
    expect(html).toContain('<ul><li>识别失败</li><li>快捷键无效</li></ul>')
  })

  it('escapes html before rendering', () => {
    const html = renderMarkdown('# 标题\n<script>alert(1)</script>')

    expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;')
    expect(html).not.toContain('<script>')
  })
})
