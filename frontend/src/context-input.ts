function splitItems(text: string): string[] {
  return text.split(/[,，]/).map((item) => item.trim()).filter(Boolean)
}

export function parseTermList(text: string): string[] {
  return splitItems(text)
}

export function parseHistoricalPasswords(text: string): string[] {
  return [...new Set(text.split(/\r?\n/).filter((item) => item.length > 0))]
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
