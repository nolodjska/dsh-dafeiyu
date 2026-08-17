import { chromium } from 'playwright-core'

const url = process.argv[2]
const screenshot = process.argv[3]
if (!url || !screenshot) throw new Error('usage: ui-acceptance <url> <screenshot>')

const browser = await chromium.launch({
  executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  headless: true,
})
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' })
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(message.text())
})

try {
  await page.goto(url, { waitUntil: 'load' })
  const continueButton = page.getByRole('button', { name: '继续', exact: true })
  if (await continueButton.waitFor({ timeout: 10_000 }).then(() => true).catch(() => false)) {
    await continueButton.click()
  }
  const configureLater = page.getByRole('button', { name: '稍后配置', exact: true })
  if (await configureLater.waitFor({ timeout: 10_000 }).then(() => true).catch(() => false)) {
    await configureLater.click()
  }
  const settingsButton = page.getByRole('button', { name: '设置', exact: true })
  await settingsButton.waitFor({ timeout: 30_000 })
  await settingsButton.click()
  const dialog = page.getByRole('dialog', { name: '设置' })
  await dialog.waitFor({ timeout: 10_000 })
  await dialog.getByRole('button', { name: '插件', exact: true }).click()
  await dialog.getByText('大肥鱼桌面伴侣', { exact: true }).waitFor({ timeout: 10_000 })
  const card = dialog.getByTestId('dsh-dafeiyu-settings')
  await card.screenshot({ path: screenshot })
  const checkboxes = card.getByRole('checkbox')
  const initialEnabled = await checkboxes.nth(0).isChecked()
  await checkboxes.nth(0).click()
  await page.waitForTimeout(400)
  const disabled = !(await checkboxes.nth(0).isChecked())
  await checkboxes.nth(0).click()
  await page.waitForTimeout(2_000)
  const reenabled = await checkboxes.nth(0).isChecked()
  console.log(JSON.stringify({
    title: await card.getByText('大肥鱼桌面伴侣', { exact: true }).innerText(),
    initialEnabled,
    disabled,
    reenabled,
    checkboxCount: await checkboxes.count(),
    sliderCount: await card.getByRole('slider').count(),
    comboboxCount: await card.getByRole('combobox').count(),
    errors,
  }))
} catch (error) {
  await page.screenshot({ path: screenshot.replace(/\.png$/i, '-failure.png'), fullPage: true }).catch(() => {})
  console.error(JSON.stringify({
    failure: error instanceof Error ? error.message : String(error),
    title: await page.title().catch(() => ''),
    body: (await page.locator('body').innerText().catch(() => '')).slice(0, 1000),
    errors,
  }))
  process.exitCode = 1
} finally {
  await browser.close()
}
