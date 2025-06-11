import { chromium } from 'playwright';

(async () => {
  // Inicia o navegador com interface visível
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();

  // Acessa a URL do produto
  await page.goto('https://www.olly.com/products/probiotic-prebiotic?variant=18073984532544', { waitUntil: "domcontentloaded" });

  // Aguarda carregamento da página (pode ajustar se necessário)
  await page.waitForLoadState("domcontentloaded");

  // Deixa o navegador aberto por um tempo (opcional)
  await page.waitForTimeout(0);

  // Fecha o navegador (opcional, pode comentar)
  // await browser.close();
})();
