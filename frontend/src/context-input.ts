function splitItems(text: string): string[] {
  return text.split(/[,，]/).map((item) => item.trim()).filter(Boolean)
}

// Parse only at submission: do not remove separators or rewrite text while typing.
export function parseContextLists(keywordText: string, yearText: string) {
  const years = splitItems(yearText).map((item) => {
    const year = Number(item)
    if (!/^\d+$/.test(item) || !Number.isSafeInteger(year) || year <= 0) {
      throw new Error('相关年份请输入正整数，并用中文或英文逗号分隔。')
    }
    return year
  })
  return { keywords: splitItems(keywordText), years }
}
