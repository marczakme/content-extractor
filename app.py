import re
import io
import time
from typing import List, Tuple, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
import streamlit as st


# ----------------------------
# Helpers
# ----------------------------
def normalize_whitespace(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_body_text(html: str) -> Tuple[str, str]:
    """
    Returns (title, cleaned_body_text).
    Cleans obvious boilerplate elements and returns text from body.
    """
    soup = BeautifulSoup(html, "lxml")

    title = ""
    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(" ", strip=True)

    body = soup.body
    if not body:
        # fallback: whole doc text
        return title, normalize_whitespace(soup.get_text("\n", strip=True))

    # Remove typical non-content elements
    for tag_name in ["script", "style", "noscript", "svg", "canvas", "iframe"]:
        for t in body.find_all(tag_name):
            t.decompose()

    # Remove typical layout/boilerplate
    for tag_name in ["header", "footer", "nav", "aside"]:
        for t in body.find_all(tag_name):
            t.decompose()

    # Remove common cookie banners / popups by heuristics (ids/classes)
    junk_selectors = [
        ("id", re.compile(r"(cookie|consent|gdpr|newsletter|popup|modal)", re.I)),
        ("class", re.compile(r"(cookie|consent|gdpr|newsletter|popup|modal|overlay)", re.I)),
    ]
    for attr, pattern in junk_selectors:
        for t in body.find_all(attrs={attr: pattern}):
            # avoid deleting whole body accidentally – only remove if it's not the body itself
            if t.name != "body":
                t.decompose()

    text = body.get_text("\n", strip=True)
    text = normalize_whitespace(text)

    return title, text


def fetch_url(url: str, timeout: int = 25, user_agent: str = "Mozilla/5.0") -> Tuple[int, str, str]:
    """
    Returns (http_status, final_url, html_text).
    Raises requests exceptions up to caller.
    """
    headers = {"User-Agent": user_agent}
    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    return r.status_code, r.url, r.text


def read_urls_from_upload(uploaded_file) -> List[str]:
    name = uploaded_file.name.lower()
    data = uploaded_file.read()

    if name.endswith(".txt"):
        text = data.decode("utf-8", errors="ignore")
        urls = [line.strip() for line in text.splitlines() if line.strip()]
        return urls

    if name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data))
        # try common column names
        for col in ["url", "URL", "adres", "Adres", "link", "Link"]:
            if col in df.columns:
                return [str(u).strip() for u in df[col].dropna().tolist() if str(u).strip()]
        # fallback: first column
        return [str(u).strip() for u in df.iloc[:, 0].dropna().tolist() if str(u).strip()]

    if name.endswith(".xlsx"):
        df = pd.read_excel(io.BytesIO(data))
        for col in ["url", "URL", "adres", "Adres", "link", "Link"]:
            if col in df.columns:
                return [str(u).strip() for u in df[col].dropna().tolist() if str(u).strip()]
        return [str(u).strip() for u in df.iloc[:, 0].dropna().tolist() if str(u).strip()]

    raise ValueError("Obsługuję tylko: TXT / CSV / XLSX z listą URL-i.")


def make_xlsx(df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="extract", index=False)
    return output.getvalue()


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="URL → BODY text → XLSX", layout="wide")

st.title("Pobieranie treści z <body> dla listy URL-i → XLSX")

with st.sidebar:
    st.header("Ustawienia")
    timeout = st.number_input("Timeout (sekundy)", min_value=5, max_value=120, value=25, step=1)
    sleep_s = st.number_input("Opóźnienie między requestami (sek.)", min_value=0.0, max_value=5.0, value=0.2, step=0.1)
    user_agent = st.text_input("User-Agent", value="Mozilla/5.0 (compatible; ContentExtractor/1.0)")
    st.caption("Tip: jeśli serwer blokuje, zmień User-Agent i zwiększ opóźnienie.")

uploaded = st.file_uploader("Wgraj plik z URL-ami (TXT/CSV/XLSX)", type=["txt", "csv", "xlsx"])

if uploaded:
    try:
        urls = read_urls_from_upload(uploaded)
        urls = list(dict.fromkeys(urls))  # dedupe preserving order
        st.success(f"Wczytano {len(urls)} URL-i.")
    except Exception as e:
        st.error(f"Nie mogę wczytać pliku: {e}")
        st.stop()

    st.write("Podgląd pierwszych 20 URL-i:")
    st.dataframe(pd.DataFrame({"url": urls[:20]}), use_container_width=True)

    if st.button("Start ekstrakcji"):
        rows = []
        progress = st.progress(0)
        status_box = st.empty()

        for i, url in enumerate(urls, start=1):
            status_box.write(f"({i}/{len(urls)}) Pobieram: {url}")

            try:
                code, final_url, html = fetch_url(url, timeout=timeout, user_agent=user_agent)
                title, body_text = extract_body_text(html)

                rows.append({
                    "input_url": url,
                    "final_url": final_url,
                    "http_status": code,
                    "title": title,
                    "body_text": body_text,
                    "body_len_chars": len(body_text),
                    "error": ""
                })

            except Exception as e:
                rows.append({
                    "input_url": url,
                    "final_url": "",
                    "http_status": "",
                    "title": "",
                    "body_text": "",
                    "body_len_chars": 0,
                    "error": str(e)
                })

            progress.progress(i / len(urls))
            if sleep_s > 0:
                time.sleep(float(sleep_s))

        df = pd.DataFrame(rows)

        st.subheader("Wyniki")
        st.dataframe(df[["input_url", "http_status", "title", "body_len_chars", "error"]], use_container_width=True)

        xlsx_bytes = make_xlsx(df)
        st.download_button(
            label="Pobierz XLSX",
            data=xlsx_bytes,
            file_name="body_extract.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        st.info("Jeśli część stron ma bardzo mało tekstu, mogą mieć content ładowany JS — wtedy trzeba użyć renderowania (Playwright/Selenium).")
else:
    st.info("Wgraj plik z listą URL-i, potem kliknij Start ekstrakcji.")
