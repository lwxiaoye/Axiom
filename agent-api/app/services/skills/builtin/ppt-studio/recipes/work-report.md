# 配方：工作汇报 / 季度总结 / OKR

适用：周报升级版、季度汇报、项目复盘、管理沟通。

## 默认参数

- 页数：8–12
- 风格：清晰、克制、浅底或浅灰底；避免过度装饰
- 场景：`reference/slides_categories/management-report.md`
- 可参考 design_system：`03_work` / `work`

## 推荐 token（浅色专业）

```yaml
theme:
  colors:
    primary: "#1F4B99"
    text: "#1A1A1A"
    muted: "#6B6B6B"
    bg: "#F7F7F5"
    panel: "#FFFFFF"
    line: "#E2E2DE"
    good: "#0F7B4B"
    bad: "#B42318"
  textStyles:
    title:
      fontFamily: {latin: "Helvetica Neue", ea: "Microsoft YaHei"}
      fontSize: 32
      color: "$text"
      bold: true
    body:
      fontFamily: {latin: "Helvetica Neue", ea: "Microsoft YaHei"}
      fontSize: 15
      color: "$text"
      lineHeight: 1.4
```

## 推荐页序

1. 封面：周期 + 团队/姓名 + 一句话结果  
2. 本期结论（先说结果，3 条以内）  
3. 目标 vs 实际（表或对照条）  
4. 关键进展（2–3 页，每页一个主题）  
5. 数据面板（少而硬）  
6. 问题与风险  
7. 下期计划 / 需要的支持  
8. 附录或来源（可选）

## 表达

- 每页一个结论句（可放在标题下金色/主色短句）
- 数字突出，过程细节收进二级文字
- 风险用 `$bad`，达成用 `$good`，不要整页红色警报风

## 不要

- 大段周报粘贴
- 超过 8 行的密集 bullet
- 无结论的过程流水账
