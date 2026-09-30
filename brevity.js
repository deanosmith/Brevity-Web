/* Per-block refresh for the Brevity site. Weather and Sky Watch load live; other blocks reload the latest published brief. */
(() => {
    "use strict";

    const configEl = document.getElementById("brevity-config");
    const config = configEl ? JSON.parse(configEl.textContent || "{}") : {};
    const TZ = "Europe/Copenhagen";
    const WIND_ARROW_SVG = '<svg viewBox="0 0 26 14" focusable="false"><path d="M1 4.6h12.2V1.6L24.8 7 13.2 12.4V9.4H1z"/></svg>';

    // ---------------------------------------------------------------- Helpers

    async function getJSON(url) {
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return response.json();
    }

    const num = (value) => {
        const n = Number(value);
        return value === null || value === undefined || value === "" || !Number.isFinite(n) ? null : n;
    };
    const round = (value) => (num(value) === null ? null : Math.round(num(value)));
    const clamp01 = (value) => Math.max(0, Math.min(1, num(value) ?? 0));

    function clock12(hour, minute = 0) {
        const h = ((hour % 24) + 24) % 24;
        return `${h % 12 || 12}:${String(minute).padStart(2, "0")} ${h < 12 ? "am" : "pm"}`;
    }

    function isoClock(stamp) {
        const match = /T(\d{2}):(\d{2})/.exec(stamp || "");
        return match ? clock12(Number(match[1]), Number(match[2])) : null;
    }

    function localParts(date) {
        const parts = {};
        new Intl.DateTimeFormat("en-GB", {
            timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit",
            weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
        }).formatToParts(date).forEach((part) => { parts[part.type] = part.value; });
        return {
            ymd: `${parts.year}-${parts.month}-${parts.day}`,
            weekday: parts.weekday,
            hour: Number(parts.hour),
            minute: Number(parts.minute),
        };
    }

    const nowClock = () => {
        const p = localParts(new Date());
        return clock12(p.hour, p.minute);
    };

    const dayDiff = (a, b) => Math.round((Date.parse(`${a}T00:00:00Z`) - Date.parse(`${b}T00:00:00Z`)) / 86400000);

    function whenLabel(date, prefix) {
        const p = localParts(date);
        const diff = dayDiff(p.ymd, localParts(new Date()).ymd);
        const clock = clock12(p.hour, p.minute);
        if (diff === 0) return `${prefix} Today At ${clock}`;
        if (diff === 1) return `${prefix} Tomorrow At ${clock}`;
        return `${prefix} ${p.weekday} At ${clock}`;
    }

    function asUtc(stamp) {
        if (!stamp) return null;
        const text = String(stamp).trim();
        const date = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(text) ? text : `${text}Z`);
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function setAll(key, text, color) {
        document.querySelectorAll(`[data-w="${key}"]`).forEach((el) => {
            el.textContent = text;
            if (color) el.style.color = color;
        });
    }

    function setField(scope, key, text, color) {
        const el = scope && scope.querySelector(`[data-f="${key}"]`);
        if (!el) return;
        el.textContent = text;
        if (color) el.style.color = color;
    }

    // ---------------------------------------------------------------- Colour scales (mirror brevity.py)

    const toHex = (rgb) => `#${rgb.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("")}`;
    const fromHex = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

    function ramp(stops, t) {
        for (let i = 0; i < stops.length - 1; i += 1) {
            const [t0, c0] = stops[i];
            const [t1, c1] = stops[i + 1];
            if (t <= t1) {
                const local = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
                return toHex(c0.map((v, k) => v + (c1[k] - v) * local));
            }
        }
        return toHex(stops[stops.length - 1][1]);
    }

    const TEMP_STOPS = [[0, [64, 148, 255]], [0.28, [90, 200, 255]], [0.5, [255, 214, 102]], [0.75, [255, 140, 66]], [1, [255, 69, 58]]];
    const UV_STOPS = [[0, [76, 175, 80]], [0.35, [255, 235, 59]], [0.6, [255, 152, 0]], [1, [244, 67, 54]]];
    const PLASMA_STOPS = [[0, "#0d0887"], [0.25, "#6a00a8"], [0.5, "#b12a90"], [0.75, "#e16462"], [1, "#f0f921"]].map(([t, c]) => [t, fromHex(c)]);

    const tempProgress = (c) => clamp01(((num(c) ?? 10) + 5) / 35);
    const tempColor = (c) => ramp(TEMP_STOPS, tempProgress(c));
    const rainColor = (p) => ramp([[0, [55, 78, 110]], [1, [64, 196, 255]]], clamp01((num(p) ?? 0) / 100));
    const uvColor = (u) => ramp(UV_STOPS, clamp01((num(u) ?? 0) / 11));
    const plasmaColor = (v) => ramp(PLASMA_STOPS, clamp01((num(v) ?? 0) / 40));

    const weatherCode = (code) => (config.weather_codes || {})[String(round(code) ?? 0)] || { text: "Unknown", icon: "?", color: "#AAAAAA" };

    // ---------------------------------------------------------------- Weather (live, Open-Meteo)

    function hourlyForDay(times, values, dayIso) {
        const byHour = {};
        (times || []).forEach((stamp, i) => {
            if (typeof stamp !== "string" || !stamp.startsWith(dayIso)) return;
            const value = num(values[i]);
            if (value !== null) byHour[Number(stamp.slice(11, 13))] = Math.round(value);
        });
        return byHour;
    }

    function peakHour(byHour, startH, endH) {
        let best = 0;
        let bestHour = null;
        for (let h = startH; h < endH; h += 1) {
            if ((byHour[h] ?? 0) > best) {
                best = byHour[h];
                bestHour = h;
            }
        }
        return bestHour === null ? null : clock12(bestHour, 0);
    }

    function updateRainTimeline(key, byHour, startH, color, headText) {
        const root = document.querySelector(`[data-rain="${key}"]`);
        if (!root) return;
        root.style.setProperty("--rain-color", color);
        root.querySelectorAll(".rain-bar").forEach((bar, i) => {
            const hour = startH + i;
            const value = byHour[hour] ?? 0;
            bar.style.setProperty("--rain", value);
            bar.title = `${clock12(hour, 0)} · ${value}%`;
        });
        setField(root, "peak", headText);
    }

    function segment(hourly, startH, endH) {
        const slice = (key) => (hourly[key] || []).slice(startH, endH).map(num).filter((v) => v !== null);
        const avg = (list) => (list.length ? list.reduce((a, b) => a + b, 0) / list.length : null);
        const codes = slice("weather_code");
        let mode = 0;
        let modeCount = 0;
        codes.forEach((c) => {
            const count = codes.filter((x) => x === c).length;
            if (count > modeCount) { mode = c; modeCount = count; }
        });
        const precips = slice("precipitation_probability");
        return {
            temp: round(avg(slice("temperature_2m"))),
            feels: round(avg(slice("apparent_temperature"))),
            precip: Math.round(precips.length ? Math.max(...precips) : 0),
            code: weatherCode(mode),
        };
    }

    function tempDialRange(high, low) {
        let lowP = clamp01(((low ?? 5) + 5) / 35);
        let highP = Math.max(lowP, clamp01(((high ?? 10) + 5) / 35));
        const minWidth = 0.015;
        if (highP - lowP < minWidth) {
            highP = Math.min(1, lowP + minWidth);
            if (highP - lowP < minWidth) lowP = Math.max(0, highP - minWidth);
        }
        return { lowP, highP };
    }

    function setDial(key, vars) {
        const el = document.querySelector(`[data-dial="${key}"]`);
        if (el) Object.entries(vars).forEach(([name, value]) => el.style.setProperty(name, value));
    }

    function weekdayLabel(dayIso, todayIso) {
        const diff = dayDiff(dayIso, todayIso);
        if (diff === 0) return "Today";
        if (diff === 1) return "Tomorrow";
        if (diff === 2) return "In 2 Days";
        return new Date(`${dayIso}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });
    }

    async function refreshWeather(section) {
        if (!section.querySelector("[data-dial]")) return refreshSnapshot(section);
        const w = config.weather || {};
        const params = new URLSearchParams({
            latitude: w.lat,
            longitude: w.lon,
            forecast_days: "3",
            timezone: w.timezone || TZ,
            temperature_unit: "celsius",
            wind_speed_unit: "kmh",
            current: "temperature_2m,apparent_temperature,weather_code,precipitation_probability,wind_speed_10m,wind_direction_10m",
            hourly: "temperature_2m,apparent_temperature,precipitation_probability,wind_speed_10m,wind_direction_10m,weather_code",
            daily: "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max,precipitation_probability_max,wind_speed_10m_max,wind_direction_10m_dominant",
        });
        const data = await getJSON(`https://api.open-meteo.com/v1/forecast?${params}`);
        const daily = data.daily || {};
        const hourly = data.hourly || {};
        const current = data.current || {};
        const days = daily.time || [];
        if (!days.length) throw new Error("Incomplete forecast");
        const at = (key, i) => num((daily[key] || [])[i]);
        const todayIso = days[0];

        const high = round(at("temperature_2m_max", 0));
        const low = round(at("temperature_2m_min", 0));
        const highColor = tempColor(high ?? 10);
        const lowColor = tempColor(low ?? 5);
        const now = weatherCode(current.weather_code ?? at("weather_code", 0));
        const nowTemp = round(current.temperature_2m);
        const feels = round(current.apparent_temperature);

        setAll("now-icon", now.icon);
        setAll("now-temp", nowTemp === null ? "—" : `${nowTemp}°`);
        document.querySelectorAll('.weather-panel [data-w="now-temp"]').forEach((el) => { el.style.color = highColor; });
        setAll("now-cond", now.text);
        setAll("now-feels", feels === null ? "" : `Feels Like ${feels}°`);
        const nowCard = section.querySelector(".weather-now-card");
        if (nowCard) nowCard.style.setProperty("--now-color", now.color);

        const sunrise = (daily.sunrise || [])[0];
        const sunset = (daily.sunset || [])[0];
        setAll("sunrise", isoClock(sunrise) || "—");
        setAll("sunset", isoClock(sunset) || "—");
        const riseMatch = /T(\d{2}):(\d{2})/.exec(sunrise || "");
        const setMatch = /T(\d{2}):(\d{2})/.exec(sunset || "");
        if (riseMatch && setMatch) {
            const span = (Number(setMatch[1]) * 60 + Number(setMatch[2])) - (Number(riseMatch[1]) * 60 + Number(riseMatch[2]));
            if (span > 0) setAll("daylight", span % 60 ? `${Math.floor(span / 60)}h ${String(span % 60).padStart(2, "0")}m` : `${span / 60}h`);
        }

        // Dials
        const range = tempDialRange(high, low);
        setDial("temp", { "--start": (range.lowP * 100).toFixed(1), "--end": (range.highP * 100).toFixed(1), "--accent": highColor });
        setAll("dial-temp-value", high === null ? "—" : `${high}°`, highColor);
        setAll("dial-temp-sub", low === null ? "" : `Low ${low}°`, lowColor);

        const rainChance = round(at("precipitation_probability_max", 0)) ?? 0;
        const todayRain = hourlyForDay(hourly.time, hourly.precipitation_probability || [], todayIso);
        const todayPeak = peakHour(todayRain, 6, 24);
        const todayRainColor = rainColor(rainChance);
        setDial("rain", { "--progress": (clamp01(rainChance / 100) * 100).toFixed(1), "--accent": todayRainColor });
        setAll("dial-rain-value", `${rainChance}%`, todayRainColor);
        setAll("dial-rain-sub", todayPeak || "");

        const wind = round(at("wind_speed_10m_max", 0));
        const windDir = round(at("wind_direction_10m_dominant", 0));
        const windColor = plasmaColor(wind ?? 0);
        setDial("wind", { "--progress": (clamp01((wind ?? 0) / (w.wind_max || 50)) * 100).toFixed(1), "--accent": windColor });
        document.querySelectorAll('[data-w="dial-wind-value"]').forEach((el) => {
            el.style.color = windColor;
            el.textContent = wind === null ? "—" : String(wind);
            if (wind !== null && windDir !== null) {
                const arrow = document.createElement("span");
                arrow.className = "wind-arrow";
                arrow.setAttribute("aria-hidden", "true");
                arrow.style.setProperty("--wind-dir", windDir);
                arrow.innerHTML = WIND_ARROW_SVG;
                el.appendChild(arrow);
            }
        });

        const uv = at("uv_index_max", 0);
        setDial("uv", { "--progress": (clamp01((uv ?? 0) / 11) * 100).toFixed(1), "--accent": uvColor(uv) });
        setAll("dial-uv-value", uv === null ? "—" : uv.toFixed(1), uvColor(uv));

        // Morning / afternoon / evening
        [["morning", 6, 12], ["afternoon", 12, 18], ["evening", 18, 24]].forEach(([key, start, end]) => {
            const card = section.querySelector(`[data-period="${key}"]`);
            if (!card) return;
            const stats = segment(hourly, start, end);
            setField(card, "icon", stats.code.icon);
            setField(card, "temp", stats.temp === null ? "—" : `${stats.temp}°`, stats.code.color);
            setField(card, "feels", stats.feels === null ? "" : `Feels ${stats.feels}°`);
            setField(card, "rain", `${stats.precip}% Rain`);
        });
        updateRainTimeline("today", todayRain, 6, todayRainColor, todayPeak ? `Rain Peak ${todayPeak}` : "No Rain Expected");

        // Next 2 days
        for (let i = 1; i < Math.min(3, days.length); i += 1) {
            const card = section.querySelector(`[data-day="${i}"]`);
            if (!card) continue;
            const code = weatherCode(at("weather_code", i));
            const dayHigh = round(at("temperature_2m_max", i));
            const dayLow = round(at("temperature_2m_min", i));
            const dayRain = round(at("precipitation_probability_max", i)) ?? 0;
            const byHour = hourlyForDay(hourly.time, hourly.precipitation_probability || [], days[i]);
            const peak = peakHour(byHour, 0, 24);
            setField(card, "label", weekdayLabel(days[i], todayIso));
            setField(card, "icon", code.icon);
            setField(card, "condition", code.text);
            setField(card, "hi", `${dayHigh ?? "—"}°`, tempColor(dayHigh ?? 10));
            setField(card, "lo", `${dayLow ?? "—"}°`, tempColor(dayLow ?? 5));
            updateRainTimeline(`day-${i}`, byHour, 0, rainColor(dayRain), `${dayRain}% Rain${peak ? ` · Peak ${peak}` : ""}`);
        }
        return { label: `Live · ${nowClock()}` };
    }

    // ---------------------------------------------------------------- Sky Watch (live, NOAA + Launch Library)

    function copenhagenTonight(now) {
        const p = localParts(now);
        if (p.hour >= 21) return now;
        return new Date(now.getTime() + ((21 - p.hour) * 60 - p.minute) * 60000);
    }

    function moonCard(now) {
        const knownNew = Date.UTC(2000, 0, 6, 18, 14);
        const synodic = 29.530588853;
        const phase = ((((now - knownNew) / 86400000) / synodic) % 1 + 1) % 1;
        const lit = Math.round(((1 - Math.cos(2 * Math.PI * phase)) / 2) * 100);
        const names = [[0.03, "New Moon"], [0.22, "Waxing Crescent"], [0.28, "First Quarter"], [0.47, "Waxing Gibbous"], [0.53, "Full Moon"], [0.72, "Waning Gibbous"], [0.78, "Last Quarter"], [0.97, "Waning Crescent"], [1.01, "New Moon"]];
        const name = (names.find(([limit]) => phase < limit) || [0, "Moon"])[1];
        return {
            name,
            detail: `Moon ${lit}% Illuminated`,
            when: "Visible Tonight Over Copenhagen",
            link: "https://moon.nasa.gov/moon-in-motion/moon-phases/",
            sortAt: copenhagenTonight(now),
        };
    }

    async function auroraCard() {
        const [kpList, ovation] = await Promise.all([
            getJSON("https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"),
            getJSON("https://services.swpc.noaa.gov/json/ovation_aurora_latest.json").catch(() => null),
        ]);
        const latest = Array.isArray(kpList) && kpList.length ? kpList[kpList.length - 1] : {};
        const kp = num(latest.estimated_kp) ?? num(latest.kp_index);
        let aurora = null;
        let bestDist = Infinity;
        ((ovation && ovation.coordinates) || []).forEach((row) => {
            if (!Array.isArray(row) || row.length < 3) return;
            const dist = Math.abs((num(row[0]) ?? 0) - 13) + Math.abs((num(row[1]) ?? 0) - 56);
            if (dist < bestDist) { bestDist = dist; aurora = num(row[2]); }
        });
        let name;
        let chance;
        if (kp === null) return null;
        if (kp >= 6 || (aurora !== null && aurora >= 20)) { name = "Aurora Watch"; chance = "Visible Aurora Possible Tonight"; }
        else if (kp >= 4) { name = "Unsettled Aurora"; chance = "Possible On The North Horizon"; }
        else { name = "Quiet Aurora"; chance = "Visible Aurora Unlikely Tonight"; }
        return {
            name,
            detail: `Geomagnetic Index Kp ${kp.toFixed(1).replace(/\.0$/, "")}`,
            when: chance,
            link: "https://www.swpc.noaa.gov/",
            sortAt: asUtc(latest.time_tag) || new Date(),
        };
    }

    function flareClass(flux) {
        const value = num(flux);
        if (value === null || value <= 0) return null;
        const bands = [[1e-4, "X"], [1e-5, "M"], [1e-6, "C"], [1e-7, "B"], [1e-8, "A"]];
        for (const [threshold, letter] of bands) {
            if (value >= threshold) {
                const magnitude = value / threshold;
                return magnitude < 10 ? `${letter}${magnitude.toFixed(1).replace(/\.0$/, "")}` : `${letter}${Math.round(magnitude)}`;
            }
        }
        return "A0";
    }

    async function solarCard() {
        const rows = await getJSON("https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json");
        const longs = (Array.isArray(rows) ? rows : []).filter((row) => row && row.energy === "0.1-0.8nm" && (num(row.flux) ?? 0) > 0);
        const last = longs[longs.length - 1] || {};
        const flare = flareClass(last.flux);
        if (!flare) return null;
        let meta = "Quiet Sun, No Major Flares";
        if (/^[XM]/.test(flare)) meta = "Active Sun, Major Flare Risk";
        else if (flare.startsWith("C")) meta = "Modest C-Class Activity";
        return {
            name: `Solar Class ${flare}`,
            detail: "Goes Satellite X-Ray Reading",
            when: meta,
            link: "https://www.swpc.noaa.gov/products/goes-x-ray-flux",
            sortAt: asUtc(last.time_tag) || new Date(),
        };
    }

    async function launchCard(now) {
        const payload = await getJSON("https://ll.thespacedevs.com/2.2.0/launch/upcoming/?limit=8&mode=list");
        const skip = new Set(["success", "failure", "partial failure"]);
        for (const item of (payload && payload.results) || []) {
            if (!item || skip.has(String((item.status || {}).name || "").toLowerCase())) continue;
            const net = asUtc(item.net);
            if (!net || net < new Date(now.getTime() - 2 * 3600000)) continue;
            const parts = String(item.name || "").split("|").map((part) => part.trim());
            const id = String(item.id || "");
            return {
                name: parts[0] || "Launch",
                detail: parts[1] ? `Mission ${parts[1]}` : "Orbital Launch",
                when: whenLabel(net, "Launch"),
                link: id ? `https://spacelaunchnow.me/launch/${encodeURIComponent(id)}/` : "https://spacelaunchnow.me/",
                sortAt: net,
            };
        }
        return null;
    }

    function eclipseCard(now) {
        const today = localParts(now).ymd;
        const events = (config.eclipses || []).map((event) => ({ ...event, when: new Date(event.when) }));
        const chosen = events.find((event) => localParts(event.when).ymd >= today);
        if (!chosen) return null;
        const delta = dayDiff(localParts(chosen.when).ymd, today);
        let when;
        if (delta === 0) when = chosen.when < now ? "Visible This Morning" : "Visible Today";
        else if (delta === 1) when = "Visible Tomorrow";
        else if (delta < 14) when = whenLabel(chosen.when, "Visible");
        else when = `Next Visible On ${chosen.when.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: TZ })}`;
        return { name: chosen.name, detail: `Visible From ${chosen.detail}`, when, link: chosen.link, sortAt: chosen.when };
    }

    function moonMilestones(now) {
        const knownNew = Date.UTC(2000, 0, 6, 18, 14);
        const synodic = 29.530588853;
        const phase = ((((now - knownNew) / 86400000) / synodic) % 1 + 1) % 1;
        return [["Full Moon", 0.5], ["New Moon", 1]].map(([label, target]) => {
            const days = (((target - phase) % 1) + 1) % 1 * synodic;
            const when = new Date(now.getTime() + days * 86400000);
            const whole = Math.round(days);
            const detail = whole === 0 ? "Tonight" : whole === 1 ? "In 1 Day" : `In ${whole} Days`;
            const dateLabel = when.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: TZ }).replace(",", "");
            return {
                name: `Next ${label}`,
                detail: `${label} ${detail}`,
                when: `On ${dateLabel}`,
                link: "https://moon.nasa.gov/moon-in-motion/moon-phases/",
                sortAt: when,
            };
        });
    }

    function skyCardNode(card) {
        const link = document.createElement("a");
        link.className = "trend-card";
        link.href = /^https:\/\//.test(card.link || "") ? card.link : "#";
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        const name = document.createElement("div");
        name.className = "trend-name";
        name.textContent = card.name;
        const meta = document.createElement("div");
        meta.className = "trend-meta";
        const detail = document.createElement("span");
        detail.className = "trend-detail";
        detail.textContent = card.detail || "";
        meta.appendChild(detail);
        if (card.when) {
            const when = document.createElement("span");
            when.className = "trend-when";
            when.textContent = card.when;
            meta.appendChild(when);
        }
        link.append(name, meta);
        return link;
    }

    async function refreshSky(section) {
        const now = new Date();
        const results = await Promise.allSettled([
            Promise.resolve(moonCard(now)),
            auroraCard(),
            solarCard(),
            launchCard(now),
            Promise.resolve(eclipseCard(now)),
        ]);
        const cards = results
            .filter((result) => result.status === "fulfilled" && result.value && result.value.name)
            .map((result) => result.value);
        if (cards.length < 3) throw new Error("Sky sources unavailable");
        for (const backup of moonMilestones(now)) {
            if (cards.length >= 5) break;
            cards.push(backup);
        }
        cards.sort((a, b) => (a.sortAt - b.sortAt) || a.name.localeCompare(b.name));
        const list = section.querySelector('[data-f="list"]');
        list.replaceChildren(...cards.slice(0, 5).map(skyCardNode));
        return { label: `Live · ${nowClock()}` };
    }

    // ---------------------------------------------------------------- Scripture (bundled verse bank)

    let versesPromise = null;

    async function refreshVerse(section) {
        if (!versesPromise) {
            versesPromise = getJSON(config.verses_url || "resources/scripture-verses.json").catch((error) => {
                versesPromise = null;
                throw error;
            });
        }
        const verses = (await versesPromise).filter((verse) => verse && verse.ref && verse.text);
        const refEl = section.querySelector('[data-f="ref"]');
        const currentRef = refEl ? refEl.textContent.trim() : "";
        const pool = verses.filter((verse) => verse.ref !== currentRef);
        if (!pool.length) throw new Error("No verses available");
        const verse = pool[Math.floor(Math.random() * pool.length)];

        const figure = section.querySelector(".verse");
        figure.hidden = false;
        const empty = section.querySelector('[data-f="empty"]');
        if (empty) empty.remove();
        setField(section, "text", verse.text);
        refEl.textContent = verse.ref;
        refEl.href = `https://www.biblegateway.com/passage/?search=${encodeURIComponent(verse.ref)}&version=KJV`;
        setField(section, "subtitle", "King James Version");
        return { label: `New Verse · ${nowClock()}` };
    }

    // ---------------------------------------------------------------- Snapshot blocks (latest published brief)

    let snapshot = null;

    function fetchSnapshot() {
        if (snapshot && Date.now() - snapshot.at < 15000) return snapshot.promise;
        const url = new URL(window.location.href);
        url.hash = "";
        url.searchParams.set("refresh", String(Date.now()));
        const promise = fetch(url.toString(), { cache: "no-store" })
            .then((response) => {
                if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
                return response.text();
            })
            .then((text) => new DOMParser().parseFromString(text, "text/html"));
        snapshot = { at: Date.now(), promise };
        promise.catch(() => { snapshot = null; });
        return promise;
    }

    function briefMeta(doc) {
        const meta = doc.querySelector('meta[name="brevity-generated"]');
        return { iso: meta ? meta.content : "", clock: meta ? meta.dataset.clock || "" : "" };
    }

    async function refreshSnapshot(section) {
        const doc = await fetchSnapshot();
        const block = section.dataset.block;
        const fresh = doc.querySelectorAll(`[data-part="${block}"]`);
        if (!fresh.length) throw new Error("Block missing from latest brief");
        document.querySelectorAll(`[data-part="${block}"]`).forEach((el, i) => {
            if (fresh[i]) el.replaceWith(document.importNode(fresh[i], true));
        });
        const latest = briefMeta(doc);
        const isNew = latest.iso && latest.iso !== briefMeta(document).iso;
        return { label: isNew ? `New Brief · ${latest.clock}` : `Up To Date · ${latest.clock || nowClock()}` };
    }

    // ---------------------------------------------------------------- Wiring

    const HANDLERS = {
        "live-weather": refreshWeather,
        "live-sky": refreshSky,
        verse: refreshVerse,
        snapshot: refreshSnapshot,
    };

    const inFlight = new WeakMap();

    function refreshSection(section) {
        if (inFlight.has(section)) return inFlight.get(section);
        const handler = HANDLERS[section.dataset.mode] || refreshSnapshot;
        const button = section.querySelector("[data-refresh]");
        const stamp = section.querySelector("[data-stamp]");
        section.classList.add("is-refreshing");
        section.classList.remove("is-error", "is-updated");
        if (button) {
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
        }
        const task = handler(section)
            .then((result) => {
                if (stamp) stamp.textContent = result.label;
                // Restart the highlight animation on repeat refreshes.
                void section.offsetWidth;
                section.classList.add("is-updated");
            })
            .catch((error) => {
                section.classList.add("is-error");
                if (stamp) {
                    stamp.textContent = "Update Failed";
                    stamp.title = error && error.message ? error.message : "Update failed";
                }
            })
            .finally(() => {
                section.classList.remove("is-refreshing");
                if (button) {
                    button.disabled = false;
                    button.removeAttribute("aria-busy");
                }
                inFlight.delete(section);
            });
        inFlight.set(section, task);
        return task;
    }

    document.addEventListener("click", (event) => {
        const button = event.target.closest("[data-refresh]");
        if (button) {
            const section = button.closest("[data-block]");
            if (section) refreshSection(section);
            return;
        }
        const all = event.target.closest("[data-refresh-all]");
        if (all) {
            all.disabled = true;
            all.classList.add("is-refreshing");
            // Scripture is excluded so Refresh All keeps today's verse.
            const sections = [...document.querySelectorAll("[data-block]")].filter((section) => section.dataset.mode !== "verse");
            Promise.allSettled(sections.map(refreshSection)).finally(() => {
                all.disabled = false;
                all.classList.remove("is-refreshing");
            });
        }
    });
})();
