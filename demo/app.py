"""RELIF demo: "is this rental expense deductible?" in the browser. No ledger, nothing saved or sent."""
import streamlit as st

from relif.demo import ask
from relif.verdict import CAPITAL_KINDS, CURRENT_KINDS, NEVER_KINDS, UNSURE_KINDS

KIND_LABELS = {k: name for k, (_, name, _) in CURRENT_KINDS.items()}
KIND_LABELS.update({"appliance": "Appliance or furniture", "improvement": "Improvement or addition",
                    "fence": "Fence", "loan_payment": "Loan or mortgage payment", "own_labour": "My own labour",
                    "penalty": "Penalty or fine", "land_transfer_tax": "Land transfer tax", "roof": "Roof",
                    "landscaping": "Landscaping", "other": "Something else"})
assert set(KIND_LABELS) == {*CURRENT_KINDS, *CAPITAL_KINDS, *NEVER_KINDS, *UNSURE_KINDS, "loan_payment"}

st.set_page_config(page_title="RELIF: is this deductible?", page_icon="🧾")
st.title("🧾 Is this rental expense deductible?")
st.caption("Not tax advice. Based on general CRA guidance (T4036). Runs in your browser; nothing is saved or sent.")

form = {}
use = st.radio("1. What is it for?", ["rental", "shared", "personal"], horizontal=True,
               format_func={"rental": "Only a rented unit", "shared": "Shared with my own unit",
                            "personal": "My own unit"}.get)
form["use"] = use
if use == "shared":
    form["rental_pct"] = st.slider("Rented share of the property (area or rooms), %", 0, 100, 50)
form["available"] = st.radio("2. Was the property available for rent then?", ["yes", "no", "unsure"],
                             horizontal=True)
kind = st.selectbox("3. What did you buy?", sorted(KIND_LABELS, key=KIND_LABELS.get), format_func=KIND_LABELS.get)
form["kind"] = kind
if kind == "loan_payment":
    form["loan_part"] = st.radio("4. Which part of the payment?", ["", "interest", "principal"], horizontal=True,
                                 format_func=lambda x: x or "Not sure")
if kind == "repair":
    form["work"] = st.radio("5. What does the work do?", ["restores", "improves", "separate_asset", "unsure"],
                            format_func={"restores": "Restores it to how it was", "improves": "Makes it better than before",
                                         "separate_asset": "Is a separate item (e.g. a new appliance)",
                                         "unsure": "Not sure"}.get)
form["personal_pct"] = st.slider("6. Personal share of this purchase, %", 0, 100, 0)
form["short_term"] = st.checkbox("7. Short-term rental (under 90 days)")
if form["short_term"]:
    form["str_compliant"] = st.radio("Does it meet local licensing rules?", ["", "yes", "no", "unsure"],
                                     horizontal=True, format_func=lambda x: x or "Don't know")
form["profit"] = st.radio("8. Do you reasonably expect a profit (market rent)?", ["yes", "no", "unsure"],
                          horizontal=True)

result = ask(form)
show = {"current": st.success, "partial": st.success, "capital": st.info, "not_deductible": st.error,
        "unsure": st.warning, "error": st.error}[result["outcome"]]
show(result["label"])
st.markdown("\n".join(f"- {line.strip()}" for line in result["lines"][1:]))
