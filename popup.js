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
    const [chart, backendData, estimatesData] = await Promise.all([
      fetchChartData(ticker, '5y', '1d'),
      fetchSummaryFromBackend(ticker),
      typeof fetchEstimatesClient === 'function' ? fetchEstimatesClient(ticker) : null,
    ]);

    if (currentPopupTicker !== ticker) return; // User closed/switched

    // Merge backend info into quote-like object
    let quote = backendData?.info || {};
    let calendarData = backendData?.calendar || null;
    let earningsHistoryData = backendData?.earningsHistory || [];

    // If backend unavailable, fall back to Supabase/snapshot analyst data
    if (!backendData && typeof fetchAnalystSummaryClient === 'function') {
      const analystSnap = await fetchAnalystSummaryClient(ticker);
      if (analystSnap) {
        // Merge analyst data fields into quote object
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
        console.log(`Popup ${ticker}: loaded analyst data from Supabase/snapshot`);
      }
    }

    const data = parseTickerData(ticker, chart, quote);
    // Add calendar and earnings data
    data.calendarEvents = calendarData;
    data.earningsHistory = earningsHistoryData;
    const summary = null; // We use backend data instead
    renderPopupContent(ticker, data, summary, chart, estimatesData);
  } catch (e) {
    console.error('Popup load error:', e);
    $popupContent.innerHTML = `<div style="color:var(--red);padding:40px;text-align:center;">Failed to load data for ${ticker}</div>`;
  }
}

function renderPopupContent(ticker, data, summary, chart, estimatesData) {
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
      revActual: e.revActual ?? null,
      revYoY: e.revYoY ?? null,
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
        <button class="chart-period-btn" data-period="Max">Max</button>
      </div>
      <div class="popup-chart-container" id="popup-chart-area"></div>
    </div>
  `;

  // Section 2: Top Line Snapshot + Guidance vs Consensus
  const est = estimatesData || {};
  html += buildTopLineSection(est);
  html += buildGuidanceSection(est, ticker);

  // Section 3: Consensus
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
    const hasRevData = surprises.some(s => s.revActual != null);
    html += `<div class="popup-section">
      <div class="popup-section-title">Earnings Surprise History</div>
      <table class="watchlist-table" style="min-width:auto;">
        <thead><tr>
          <th>Quarter</th>
          <th class="num">Actual EPS</th>
          <th class="num">Est. EPS</th>
          <th class="num">EPS Surprise</th>
          ${hasRevData ? '<th class="num">Revenue</th><th class="num">Rev YoY</th>' : ''}
        </tr></thead>
        <tbody>`;
    surprises.forEach(s => {
      const revStr = s.revActual != null ? formatLargeNumber(s.revActual) : '—';
      const revYoYStr = s.revYoY != null
        ? `<span class="${percentClass(s.revYoY * 100)}">${(s.revYoY * 100) >= 0 ? '+' : ''}${(s.revYoY * 100).toFixed(1)}%</span>`
        : '—';
      html += `<tr>
        <td style="color:var(--text-secondary);">${s.date}</td>
        <td class="num">${s.actual != null ? '$' + s.actual.toFixed(2) : '—'}</td>
        <td class="num">${s.estimate != null ? '$' + s.estimate.toFixed(2) : '—'}</td>
        <td class="num ${percentClass(s.surprise)}">${s.surprise != null ? formatPercent(s.surprise) : '—'}</td>
        ${hasRevData ? `<td class="num">${revStr}</td><td class="num">${revYoYStr}</td>` : ''}
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

// --- Top Line Snapshot section ---
function buildTopLineSection(est) {
  if (!est || (!est.revenueLtm && !est.nextQRevEst)) return '';

  const fmtBig = (v) => v != null ? formatLargeNumber(v) : '—';
  const fmtPct = (v) => {
    if (v == null) return '—';
    const pct = (v * 100);
    return `<span class="${percentClass(pct)}">${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%</span>`;
  };
  const fmtEps = (v) => v != null ? (v >= 0 ? '$' + v.toFixed(2) : '-$' + Math.abs(v).toFixed(2)) : '—';

  let html = `<div class="popup-section">
    <div class="popup-section-title">Top Line Snapshot</div>
    <div class="metrics-grid metrics-grid-4">`;

  // Row 1: LTM Revenue, Rev Growth, Gross Margin, Op Margin
  html += `
      <div class="metric-card">
        <div class="metric-label">LTM Revenue</div>
        <div class="metric-value">${fmtBig(est.revenueLtm)}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Rev Growth (YoY)</div>
        <div class="metric-value">${fmtPct(est.revenueGrowth)}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Gross Margin</div>
        <div class="metric-value">${est.grossMargins != null ? (est.grossMargins * 100).toFixed(1) + '%' : '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Op Margin</div>
        <div class="metric-value">${est.operatingMargins != null ? (est.operatingMargins * 100).toFixed(1) + '%' : '—'}</div>
      </div>`;

  // Row 2: FCF, FCF Margin, NQ Rev Est, NQ EPS Est
  html += `
      <div class="metric-card">
        <div class="metric-label">FCF (LTM)</div>
        <div class="metric-value">${fmtBig(est.fcf)}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">FCF Margin</div>
        <div class="metric-value">${est.fcfMargin != null ? (est.fcfMargin * 100).toFixed(1) + '%' : '—'}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">NQ Rev Est</div>
        <div class="metric-value">${fmtBig(est.nextQRevEst)}</div>
        ${est.nextQRevGrowth != null ? `<div class="metric-sub">${fmtPct(est.nextQRevGrowth)} YoY</div>` : ''}
      </div>
      <div class="metric-card">
        <div class="metric-label">NQ EPS Est</div>
        <div class="metric-value">${fmtEps(est.nextQEpsEst)}</div>
        ${est.nextQEpsGrowth != null ? `<div class="metric-sub">${fmtPct(est.nextQEpsGrowth)} YoY</div>` : ''}
      </div>`;

  // Row 3: FY1 & FY2 estimates
  html += `
      <div class="metric-card">
        <div class="metric-label">FY1 Rev Est</div>
        <div class="metric-value">${fmtBig(est.fy1RevEst)}</div>
        ${est.fy1RevGrowth != null ? `<div class="metric-sub">${fmtPct(est.fy1RevGrowth)} YoY</div>` : ''}
      </div>
      <div class="metric-card">
        <div class="metric-label">FY1 EPS Est</div>
        <div class="metric-value">${fmtEps(est.fy1EpsEst)}</div>
        ${est.fy1EpsGrowth != null ? `<div class="metric-sub">${fmtPct(est.fy1EpsGrowth)} YoY</div>` : ''}
      </div>
      <div class="metric-card">
        <div class="metric-label">FY2 Rev Est</div>
        <div class="metric-value">${fmtBig(est.fy2RevEst)}</div>
        ${est.fy2RevGrowth != null ? `<div class="metric-sub">${fmtPct(est.fy2RevGrowth)} YoY</div>` : ''}
      </div>
      <div class="metric-card">
        <div class="metric-label">FY2 EPS Est</div>
        <div class="metric-value">${fmtEps(est.fy2EpsEst)}</div>
        ${est.fy2EpsGrowth != null ? `<div class="metric-sub">${fmtPct(est.fy2EpsGrowth)} YoY</div>` : ''}
      </div>`;

  html += `</div></div>`;
  return html;
}

// --- Guidance vs Consensus section ---
function buildGuidanceSection(est, ticker) {
  // Only show if we have guidance data
  if (!est || (est.guideRevHigh == null && est.guideEpsHigh == null && est.epsTrendCurrent == null)) return '';

  const fmtBig = (v) => v != null ? formatLargeNumber(v) : '—';
  const fmtEps = (v) => v != null ? (v >= 0 ? '$' + v.toFixed(2) : '-$' + Math.abs(v).toFixed(2)) : '—';

  let html = `<div class="popup-section">
    <div class="popup-section-title">Guidance vs Consensus</div>`;

  // Guidance table (next quarter)
  if (est.guideRevHigh != null || est.guideEpsHigh != null) {
    // Determine if consensus sits inside or outside guidance range
    let revSignal = '';
    if (est.consensusRev != null && est.guideRevLow != null && est.guideRevHigh != null) {
      if (est.consensusRev > est.guideRevHigh) revSignal = '<span class="signal-above">Above guide</span>';
      else if (est.consensusRev < est.guideRevLow) revSignal = '<span class="signal-below">Below guide</span>';
      else {
        const mid = (est.guideRevLow + est.guideRevHigh) / 2;
        const pctOfRange = (est.consensusRev - est.guideRevLow) / (est.guideRevHigh - est.guideRevLow);
        if (pctOfRange > 0.7) revSignal = '<span class="signal-high">High end</span>';
        else if (pctOfRange < 0.3) revSignal = '<span class="signal-low">Low end</span>';
        else revSignal = '<span class="signal-mid">Mid-range</span>';
      }
    }

    let epsSignal = '';
    if (est.consensusEps != null && est.guideEpsLow != null && est.guideEpsHigh != null) {
      if (est.consensusEps > est.guideEpsHigh) epsSignal = '<span class="signal-above">Above guide</span>';
      else if (est.consensusEps < est.guideEpsLow) epsSignal = '<span class="signal-below">Below guide</span>';
      else {
        const range = est.guideEpsHigh - est.guideEpsLow;
        if (range > 0) {
          const pct = (est.consensusEps - est.guideEpsLow) / range;
          if (pct > 0.7) epsSignal = '<span class="signal-high">High end</span>';
          else if (pct < 0.3) epsSignal = '<span class="signal-low">Low end</span>';
          else epsSignal = '<span class="signal-mid">Mid-range</span>';
        }
      }
    }

    html += `
    <table class="guide-table">
      <thead><tr>
        <th>Next Quarter</th>
        <th>Guide Low</th>
        <th>Guide High</th>
        <th>Street Consensus</th>
        <th>Signal</th>
      </tr></thead>
      <tbody>
        <tr>
          <td class="guide-metric">Revenue</td>
          <td>${fmtBig(est.guideRevLow)}</td>
          <td>${fmtBig(est.guideRevHigh)}</td>
          <td>${fmtBig(est.consensusRev)}</td>
          <td>${revSignal}</td>
        </tr>
        <tr>
          <td class="guide-metric">EPS</td>
          <td>${fmtEps(est.guideEpsLow)}</td>
          <td>${fmtEps(est.guideEpsHigh)}</td>
          <td>${fmtEps(est.consensusEps)}</td>
          <td>${epsSignal}</td>
        </tr>
      </tbody>
    </table>`;
  }

  // EPS Revision Momentum
  if (est.epsTrendCurrent != null) {
    const trend = [
      { label: 'Current', val: est.epsTrendCurrent },
      { label: '7d Ago', val: est.epsTrend7d },
      { label: '30d Ago', val: est.epsTrend30d },
      { label: '60d Ago', val: est.epsTrend60d },
      { label: '90d Ago', val: est.epsTrend90d },
    ].filter(t => t.val != null);

    // Calculate direction of revisions
    let revisionDirection = '';
    if (trend.length >= 2) {
      const delta = trend[0].val - trend[trend.length - 1].val;
      if (Math.abs(delta) < 0.001) revisionDirection = 'Flat';
      else if (delta > 0) revisionDirection = `<span class="val-pos">+$${delta.toFixed(2)} over ${trend.length > 2 ? '90' : '7'}d</span>`;
      else revisionDirection = `<span class="val-neg">-$${Math.abs(delta).toFixed(2)} over ${trend.length > 2 ? '90' : '7'}d</span>`;
    }

    html += `
    <div class="revision-section">
      <div class="revision-header">
        <span class="revision-title">EPS Revision Momentum (Next Q)</span>
        <span class="revision-direction">${revisionDirection}</span>
      </div>
      <div class="revision-track">`;

    trend.forEach((t, i) => {
      const isFirst = i === 0;
      html += `
        <div class="revision-point ${isFirst ? 'revision-current' : ''}">
          <div class="revision-val">${fmtEps(t.val)}</div>
          <div class="revision-label">${t.label}</div>
        </div>`;
    });

    html += `</div>`;

    // Analyst revision counts
    if (est.revisionsUp30d != null || est.revisionsDown30d != null) {
      const up = est.revisionsUp30d || 0;
      const down = est.revisionsDown30d || 0;
      const total = up + down;
      const upPct = total > 0 ? Math.round((up / total) * 100) : 0;
      html += `
      <div class="revision-counts">
        <span class="revision-up">\u25B2 ${up} up</span>
        <span class="revision-bar"><span class="revision-bar-fill" style="width:${upPct}%"></span></span>
        <span class="revision-down">\u25BC ${down} down</span>
        <span class="revision-period">last 30d</span>
      </div>`;
    }

    html += `</div>`;
  }

  // Revenue Revision Momentum
  if (est.nextQRevEst != null || est.fy1RevEst != null) {
    html += `
    <div class="revision-section">
      <div class="revision-header">
        <span class="revision-title">Revenue Estimate Ranges</span>
      </div>`;

    // Build rows: NQ, FY1, FY2
    const revRows = [
      { label: 'Next Q', est: est.nextQRevEst, low: est.nextQRevLow, high: est.nextQRevHigh, growth: est.nextQRevGrowth, n: est.nextQRevAnalysts, spread: est.nextQRevSpread },
      { label: 'FY1', est: est.fy1RevEst, low: est.fy1RevLow, high: est.fy1RevHigh, growth: est.fy1RevGrowth, n: est.fy1RevAnalysts, spread: est.fy1RevSpread },
      { label: 'FY2', est: est.fy2RevEst, low: est.fy2RevLow, high: est.fy2RevHigh, growth: est.fy2RevGrowth, n: est.fy2RevAnalysts, spread: est.fy2RevSpread },
    ].filter(r => r.est != null);

    if (revRows.length > 0) {
      html += `
      <table class="guide-table rev-range-table">
        <thead><tr>
          <th>Period</th>
          <th>Low</th>
          <th>Consensus</th>
          <th>High</th>
          <th>YoY Growth</th>
          <th>Spread</th>
        </tr></thead>
        <tbody>`;

      revRows.forEach(r => {
        // Spread classification for color coding
        let spreadClass = '';
        let spreadLabel = '';
        if (r.spread != null) {
          if (r.spread < 0.03) { spreadClass = 'spread-tight'; spreadLabel = 'Tight'; }
          else if (r.spread < 0.08) { spreadClass = 'spread-normal'; spreadLabel = ''; }
          else { spreadClass = 'spread-wide'; spreadLabel = 'Wide'; }
        }

        const growthHtml = r.growth != null
          ? `<span class="${percentClass(r.growth * 100)}">${(r.growth * 100) >= 0 ? '+' : ''}${(r.growth * 100).toFixed(1)}%</span>`
          : '\u2014';

        html += `
          <tr>
            <td class="guide-metric">${r.label}</td>
            <td>${fmtBig(r.low)}</td>
            <td><strong>${fmtBig(r.est)}</strong></td>
            <td>${fmtBig(r.high)}</td>
            <td>${growthHtml}</td>
            <td><span class="${spreadClass}">${r.spread != null ? (r.spread * 100).toFixed(1) + '%' : '\u2014'}${spreadLabel ? ' <small>' + spreadLabel + '</small>' : ''}</span></td>
          </tr>`;
      });

      html += `</tbody></table>`;
    }

    // FY1 EPS revision trend (annual view)
    if (est.fy1EpsTrendCurrent != null) {
      const fy1Trend = [
        { label: 'Current', val: est.fy1EpsTrendCurrent },
        { label: '7d Ago', val: est.fy1EpsTrend7d },
        { label: '30d Ago', val: est.fy1EpsTrend30d },
        { label: '60d Ago', val: est.fy1EpsTrend60d },
        { label: '90d Ago', val: est.fy1EpsTrend90d },
      ].filter(t => t.val != null);

      if (fy1Trend.length >= 2) {
        const delta = fy1Trend[0].val - fy1Trend[fy1Trend.length - 1].val;
        let fy1Dir = 'Flat';
        if (Math.abs(delta) >= 0.001) {
          fy1Dir = delta > 0
            ? `<span class="val-pos">+$${delta.toFixed(2)} over 90d</span>`
            : `<span class="val-neg">-$${Math.abs(delta).toFixed(2)} over 90d</span>`;
        }

        html += `
        <div style="margin-top:10px;">
          <div class="revision-header">
            <span class="revision-title">FY1 EPS Revision Momentum</span>
            <span class="revision-direction">${fy1Dir}</span>
          </div>
          <div class="revision-track">`;

        fy1Trend.forEach((t, i) => {
          html += `
            <div class="revision-point ${i === 0 ? 'revision-current' : ''}">
              <div class="revision-val">${fmtEps(t.val)}</div>
              <div class="revision-label">${t.label}</div>
            </div>`;
        });

        html += `</div>`;

        // FY1 revision counts
        if (est.fy1RevisionsUp30d != null || est.fy1RevisionsDown30d != null) {
          const up = est.fy1RevisionsUp30d || 0;
          const down = est.fy1RevisionsDown30d || 0;
          const total = up + down;
          const upPct = total > 0 ? Math.round((up / total) * 100) : 0;
          html += `
          <div class="revision-counts">
            <span class="revision-up">\u25B2 ${up} up</span>
            <span class="revision-bar"><span class="revision-bar-fill" style="width:${upPct}%"></span></span>
            <span class="revision-down">\u25BC ${down} down</span>
            <span class="revision-period">last 30d</span>
          </div>`;
        }

        html += `</div>`;
      }
    }

    html += `</div>`;
  }

  html += `</div>`;
  return html;
}
