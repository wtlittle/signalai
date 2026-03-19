/* ===== POPUP.JS — Ticker detail popup logic ===== */

const $popupOverlay = document.getElementById('popup-overlay');
const $popupModal = document.getElementById('popup-modal');
const $popupContent = document.getElementById('popup-content');
const $popupClose = document.getElementById('popup-close');

let currentPopupTicker = null;

// Close popup
$popupClose.addEventListener('click', closePopup);
$popupOverlay.addEventListener('click', (e) => {
  if (e.target === $popupOverlay) closePopup();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closePopup();
});

function closePopup() {
  $popupOverlay.classList.remove('active');
  currentPopupTicker = null;
  if (popupChartInstance) {
    popupChartInstance.destroy();
    popupChartInstance = null;
  }
  // Clean up deep dive state
  deepDiveExpanded = false;
  destroyDeepDiveCharts();
}

async function openPopup(ticker) {
  currentPopupTicker = ticker;
  $popupOverlay.classList.add('active');
  $popupContent.innerHTML = '<div class="popup-loading">Loading data</div>';

  try {
    // Fetch fresh data from chart + backend
    const [chart, backendData] = await Promise.all([
      fetchChartData(ticker, '5y', '1d'),
      fetchSummaryFromBackend(ticker),
    ]);

    if (currentPopupTicker !== ticker) return; // User closed/switched

    // Merge backend info into quote-like object
    let quote = backendData?.info || {};
    let calendarData = backendData?.calendar || null;
    let earningsHistoryData = backendData?.earningsHistory || [];

    // If backend unavailable, fall back to snapshot analyst data
    if (!backendData && typeof loadSnapshot === 'function') {
      const snap = await loadSnapshot();
      const analystSnap = snap?.analyst_summary?.[ticker];
      if (analystSnap) {
        // Merge analyst snapshot fields into quote object
        quote = { ...quote };
        const analystFields = [
          'targetMeanPrice', 'targetHighPrice', 'targetLowPrice', 'targetMedianPrice',
          'numberOfAnalystOpinions', 'recommendationKey', 'recommendationMean',
          'forwardEps', 'trailingEps', 'forwardPE', 'trailingPE',
          'beta', 'averageVolume', 'averageVolume10days', 'volume',
          'fiftyTwoWeekHigh', 'fiftyTwoWeekLow', 'sharesOutstanding',
        ];
        for (const field of analystFields) {
          if (analystSnap[field] != null && !quote[field]) {
            quote[field] = analystSnap[field];
          }
        }
        calendarData = analystSnap.calendar || null;
        earningsHistoryData = analystSnap.earningsHistory || [];
        console.log(`Popup ${ticker}: loaded analyst data from snapshot`);
      }
    }

    const data = parseTickerData(ticker, chart, quote);
    // Add calendar and earnings data
    data.calendarEvents = calendarData;
    data.earningsHistory = earningsHistoryData;
    const summary = null; // We use backend data instead
    renderPopupContent(ticker, data, summary, chart);
  } catch (e) {
    console.error('Popup load error:', e);
    $popupContent.innerHTML = `<div style="color:var(--red);padding:40px;text-align:center;">Failed to load data for ${ticker}</div>`;
  }
}

function renderPopupContent(ticker, data, summary, chart) {
  // Data is now primarily from backend
  const cal = data.calendarEvents || {};
  const earningsHistory = data.earningsHistory || [];

  // Price and change
  const price = data.price;
  const change1d = data.d1;

  // Target prices (from backend data)
  const targetMean = data.targetMeanPrice;
  const targetHigh = data.targetHighPrice;
  const targetLow = data.targetLowPrice;
  const numAnalysts = data.numberOfAnalystOpinions;
  const recKey = data.recommendationKey;

  // 52-week
  const high52 = data.fiftyTwoWeekHigh;
  const low52 = data.fiftyTwoWeekLow;
  const pctFrom52High = (price && high52) ? ((price - high52) / high52) * 100 : null;

  // Volume
  const avgVol = data.averageVolume;
  const curVol = data.volume;
  const volRatio = (curVol && avgVol) ? curVol / avgVol : null;

  // Beta
  const beta = data.beta;

  // Next earnings date from calendar
  let nextEarningsDate = null;
  const earningsDateField = cal['Earnings Date'] || cal['earnings_date'];
  if (earningsDateField) {
    try {
      // Could be an array of ISO strings or a single string
      let dateStr = Array.isArray(earningsDateField) ? earningsDateField[0] : earningsDateField;
      // Clean up Python repr if needed
      if (typeof dateStr === 'string') {
        dateStr = dateStr.replace(/[\[\]]/g, '').replace(/datetime\.date\((\d+),\s*(\d+),\s*(\d+)\)/, '$1-$2-$3');
      }
      const parsed = new Date(dateStr);
      if (!isNaN(parsed.getTime())) nextEarningsDate = parsed;
    } catch(e) {}
  }
  const daysToEarnings = nextEarningsDate && !isNaN(nextEarningsDate.getTime())
    ? Math.ceil((nextEarningsDate - new Date()) / (1000 * 60 * 60 * 24)) : null;

  // Earnings surprises from history
  const surprises = earningsHistory.map(e => {
    let dateStr = e.Quarter || e.date || e.quarter || '—';
    // Format date if it's ISO
    if (dateStr && dateStr.includes('-') && dateStr.length >= 10) {
      try {
        const d = new Date(dateStr);
        if (!isNaN(d.getTime())) {
          dateStr = d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
        }
      } catch(ex) {}
    }
    return {
      date: dateStr,
      actual: e.epsActual ?? e.actual,
      estimate: e.epsEstimate ?? e.estimate,
      surprise: e.surprisePercent ?? e.surprise,
    };
  });
  const beatCount = surprises.filter(s => s.surprise > 0).length;
  const avgSurprise = surprises.length > 0 ?
    surprises.reduce((sum, s) => sum + (s.surprise || 0), 0) / surprises.length : null;

  // Forward estimates
  const fwdEPS = data.forwardEps;
  const fwdRev = data.totalRevenue;

  // --- Build HTML ---
  let html = '';

  // Header
  html += `
    <div class="popup-header">
      <span class="popup-ticker">${ticker}</span>
      <span class="popup-company">${getCommonName(ticker, data.name)}</span>
      <div class="popup-price-group">
        <span class="popup-price">${formatPrice(price)}</span>
        <span class="popup-change ${percentClass(change1d)}">${formatPercent(change1d)}</span>
      </div>
    </div>
  `;

  // Section 1: Chart
  html += `
    <div class="popup-section">
      <div class="popup-section-title">Performance Comparison</div>
      <div class="chart-period-btns" id="chart-period-btns">
        <button class="chart-period-btn" data-period="1M">1M</button>
        <button class="chart-period-btn" data-period="3M">3M</button>
        <button class="chart-period-btn" data-period="6M">6M</button>
        <button class="chart-period-btn active" data-period="1Y">1Y</button>
        <button class="chart-period-btn" data-period="3Y">3Y</button>
        <button class="chart-period-btn" data-period="5Y">5Y</button>
      </div>
      <div class="popup-chart-container" id="popup-chart-area"></div>
    </div>
  `;

  // Section 2: Consensus
  html += `<div class="popup-section">
    <div class="popup-section-title">What the Street Thinks</div>
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">Target (Mean)</div>
        <div class="metric-value">${targetMean ? formatPrice(targetMean) : '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Target (High / Low)</div>
        <div class="metric-value">${targetHigh ? formatPrice(targetHigh) : '—'} / ${targetLow ? formatPrice(targetLow) : '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Analysts</div>
        <div class="metric-value">${numAnalysts ?? '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Recommendation</div>
        <div class="metric-value" style="text-transform:capitalize;">${recKey || '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Upside/Downside</div>
        <div class="metric-value ${percentClass(targetMean && price ? ((targetMean - price) / price) * 100 : null)}">
          ${(targetMean && price) ? formatPercent(((targetMean - price) / price) * 100) : '—'}
        </div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Fwd EPS Est.</div>
        <div class="metric-value">${fwdEPS != null ? '$' + fwdEPS.toFixed(2) : '—'}</div>
      </div>
    </div>
    <div class="narrative-text">${generateConsensusNarrative(ticker, data, { targetMean, targetHigh, targetLow, numAnalysts, recKey, surprises, beatCount, avgSurprise, fwdEPS, fwdRev })}</div>
  </div>`;

  // Section 3: Technical Summary
  html += `<div class="popup-section">
    <div class="popup-section-title">Technical / Trading Summary</div>
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">52-Week High</div>
        <div class="metric-value">${high52 ? formatPrice(high52) : '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">52-Week Low</div>
        <div class="metric-value">${low52 ? formatPrice(low52) : '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">% From 52W High</div>
        <div class="metric-value ${percentClass(pctFrom52High)}">${pctFrom52High != null ? formatPercent(pctFrom52High) : '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Beta</div>
        <div class="metric-value">${beta != null ? beta.toFixed(2) : '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Avg Volume</div>
        <div class="metric-value">${avgVol ? formatLargeNumber(avgVol).replace('$', '') : '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Volume / Avg</div>
        <div class="metric-value">${volRatio != null ? volRatio.toFixed(2) + 'x' : '—'}</div>
      </div>
    </div>
    <div class="narrative-text">${generateTechnicalNarrative({ price, high52, low52, pctFrom52High, volRatio, beta })}</div>
  </div>`;

  // Section 4: Events Calendar
  html += `<div class="popup-section">
    <div class="popup-section-title">Upcoming Events</div>
    <ul class="events-list">`;

  if (nextEarningsDate) {
    html += `<li><span class="event-date">${nextEarningsDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</span><span class="event-type"><strong>Earnings Report</strong>${daysToEarnings != null ? ` (${daysToEarnings} days)` : ''}</span></li>`;
  }
  // Ex-dividend date
  const exDivDate = cal.exDividendDate?.raw;
  if (exDivDate) {
    html += `<li><span class="event-date">${formatDate(exDivDate)}</span><span class="event-type">Ex-Dividend Date</span></li>`;
  }
  if (!nextEarningsDate && !exDivDate) {
    html += `<li style="color:var(--text-muted);">No upcoming events found</li>`;
  }
  html += `</ul></div>`;

  // Section 5: Pre-Earnings Narrative (conditional)
  if (daysToEarnings != null && daysToEarnings >= 0 && daysToEarnings <= 14) {
    html += `<div class="popup-section">
      <div class="popup-section-title" style="color:var(--accent);">⚡ Heading Into the Print</div>
      <div class="narrative-text">${generatePreEarningsNarrative(ticker, data, {
        nextEarningsDate, fwdEPS, fwdRev, surprises, beatCount, avgSurprise, numAnalysts, recKey, daysToEarnings
      })}</div>
    </div>`;
  }

  // Earnings history table
  if (surprises.length > 0) {
    html += `<div class="popup-section">
      <div class="popup-section-title">Earnings Surprise History</div>
      <table class="watchlist-table" style="min-width:auto;">
        <thead><tr>
          <th>Quarter</th>
          <th class="num">Actual EPS</th>
          <th class="num">Estimate</th>
          <th class="num">Surprise %</th>
        </tr></thead>
        <tbody>`;
    surprises.forEach(s => {
      html += `<tr>
        <td style="color:var(--text-secondary);">${s.date}</td>
        <td class="num">${s.actual != null ? '$' + s.actual.toFixed(2) : '—'}</td>
        <td class="num">${s.estimate != null ? '$' + s.estimate.toFixed(2) : '—'}</td>
        <td class="num ${percentClass(s.surprise)}">${s.surprise != null ? formatPercent(s.surprise) : '—'}</td>
      </tr>`;
    });
    html += `</tbody></table></div>`;
  }

  // Add See More button
  html += createSeeMoreButton();

  $popupContent.innerHTML = html;

  // Initialize chart
  const chartArea = document.getElementById('popup-chart-area');
  if (chartArea) {
    renderPopupChart(chartArea, ticker, '1Y');
  }

  // Period button handlers
  const btns = document.querySelectorAll('#chart-period-btns .chart-period-btn');
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      btns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderPopupChart(chartArea, ticker, btn.dataset.period);
    });
  });

  // See More button handler
  const seeMoreBtn = document.getElementById('see-more-btn');
  if (seeMoreBtn) {
    seeMoreBtn.addEventListener('click', () => {
      toggleDeepDive(ticker, data);
    });
  }
}

// --- Narrative generators ---
function generateConsensusNarrative(ticker, data, info) {
  const parts = [];
  const { targetMean, numAnalysts, recKey, surprises, beatCount, avgSurprise, fwdRev } = info;
  const price = data.price;

  if (targetMean && price) {
    const upsideDown = ((targetMean - price) / price) * 100;
    if (upsideDown < -5) {
      parts.push(`${ticker} is trading above the consensus target of ${formatPrice(targetMean)}, suggesting the Street may need to raise estimates.`);
    } else if (upsideDown > 15) {
      parts.push(`Trading at a ${Math.abs(upsideDown).toFixed(0)}% discount to the consensus target of ${formatPrice(targetMean)}, suggesting meaningful potential upside if fundamentals hold.`);
    } else if (upsideDown > 0) {
      parts.push(`The consensus price target of ${formatPrice(targetMean)} implies ${upsideDown.toFixed(0)}% upside from current levels.`);
    } else {
      parts.push(`Trading roughly in line with the consensus target of ${formatPrice(targetMean)}.`);
    }
  }

  if (numAnalysts) {
    parts.push(`${numAnalysts} analyst${numAnalysts > 1 ? 's' : ''} cover the stock with a consensus "${recKey || 'N/A'}" rating.`);
  }

  if (surprises.length > 0) {
    parts.push(`The company beat EPS estimates in ${beatCount} of the last ${surprises.length} quarters` +
      (avgSurprise != null ? `, with an average surprise of ${avgSurprise >= 0 ? '+' : ''}${avgSurprise.toFixed(1)}%.` : '.'));
  }

  if (fwdRev) {
    parts.push(`Forward revenue estimate: ${formatLargeNumber(fwdRev)}.`);
  }

  return parts.join(' ') || 'Insufficient data to generate narrative.';
}

function generateTechnicalNarrative(info) {
  const { price, high52, pctFrom52High, volRatio, beta } = info;
  const parts = [];

  if (pctFrom52High != null) {
    if (Math.abs(pctFrom52High) <= 5) {
      parts.push('Trading near 52-week highs.');
    } else if (pctFrom52High < -20) {
      parts.push(`In correction territory, ${Math.abs(pctFrom52High).toFixed(1)}% below the 52-week high of ${formatPrice(high52)}.`);
    } else if (pctFrom52High < -10) {
      parts.push(`Pulled back ${Math.abs(pctFrom52High).toFixed(1)}% from the 52-week high of ${formatPrice(high52)}.`);
    } else {
      parts.push(`${Math.abs(pctFrom52High).toFixed(1)}% below the 52-week high.`);
    }
  }

  if (volRatio != null) {
    if (volRatio > 2) {
      parts.push(`Volume is elevated at ${volRatio.toFixed(1)}x the average — significant institutional activity.`);
    } else if (volRatio > 1.3) {
      parts.push(`Volume is above average at ${volRatio.toFixed(1)}x.`);
    } else if (volRatio < 0.5) {
      parts.push(`Volume is light at ${volRatio.toFixed(1)}x the average.`);
    } else {
      parts.push(`Volume is tracking near average levels.`);
    }
  }

  if (beta != null) {
    if (beta > 1.5) {
      parts.push(`High beta of ${beta.toFixed(2)} indicates above-average market sensitivity.`);
    } else if (beta < 0.7) {
      parts.push(`Low beta of ${beta.toFixed(2)} suggests relatively defensive behavior.`);
    }
  }

  return parts.join(' ') || 'Insufficient data for technical commentary.';
}

function generatePreEarningsNarrative(ticker, data, info) {
  const { nextEarningsDate, fwdEPS, fwdRev, surprises, beatCount, avgSurprise, numAnalysts, recKey, daysToEarnings } = info;
  const parts = [];

  const dateStr = nextEarningsDate ? nextEarningsDate.toLocaleDateString('en-US', { month: 'long', day: 'numeric' }) : 'soon';
  parts.push(`${ticker} reports on ${dateStr}${daysToEarnings === 0 ? ' (today)' : daysToEarnings === 1 ? ' (tomorrow)' : ` (${daysToEarnings} days)`}.`);

  if (fwdEPS != null || fwdRev != null) {
    const epsPart = fwdEPS != null ? `$${fwdEPS.toFixed(2)} EPS` : '';
    const revPart = fwdRev != null ? `${formatLargeNumber(fwdRev)} revenue` : '';
    const connector = epsPart && revPart ? ' on ' : '';
    parts.push(`Consensus expects ${epsPart}${connector}${revPart}.`);
  }

  if (surprises.length > 0) {
    parts.push(`The company has beaten estimates in ${beatCount} of the last ${surprises.length} quarters` +
      (avgSurprise != null ? ` by an average of ${Math.abs(avgSurprise).toFixed(1)}%.` : '.'));
  }

  if (data.m1 != null) {
    parts.push(`The stock is ${data.m1 >= 0 ? 'up' : 'down'} ${Math.abs(data.m1).toFixed(1)}% over the past month heading into the print.`);
  }

  if (numAnalysts) {
    parts.push(`${numAnalysts} analysts cover the stock with a consensus "${recKey || 'N/A'}" rating.`);
  }

  return parts.join(' ');
}
