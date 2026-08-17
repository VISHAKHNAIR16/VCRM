/**
 * cart-model.js
 * ─────────────────────────────────────────────────────────────────────────
 * THE UNIFIED CART DATA MODEL
 *
 * This is the single contract every cart item in the merged quotation tool
 * must satisfy, regardless of which product type it represents. It exists
 * so quotation.html's cart rendering, totals, and quotation-text/export
 * logic can treat every line item the same way, while still carrying
 * enough type-specific detail to render a good card and rebuild pricing
 * if needed (e.g. re-pricing after a pax-count change).
 *
 * WHY A SEPARATE FILE
 * ────────────────────
 * Previously, cart items were built ad hoc inline inside
 * `addToCartFromDetail()` in quotation.html, with a shape implicitly
 * defined by whatever fields that one function happened to set. That was
 * fine when there was only one product type. Now that a cart item can be
 * a transfer, an attraction ticket, OR an attraction+transfer bundle, the
 * shape needs to be explicit and centrally defined — otherwise each new
 * item type silently invents its own slightly-different fields and the
 * rendering/export code fills up with type-sniffing guesswork.
 *
 * THE CONTRACT
 * ────────────
 * Every cart item — of ANY type — has these common fields:
 *
 *   {
 *     id:          string,   // unique cart-line id (not the product id)
 *     type:        "transfer" | "attraction" | "attraction_bundle",
 *     name:        string,   // display title for the cart line
 *     totalPrice:  number,   // THB, fully inclusive of addons/transfer/etc.
 *     addons:      Array<{name: string, price: number}>,
 *     addonTotal:  number,   // THB, sum of `addons[].price`
 *     quantity:    number,   // reserved for future multi-qty support; always 1 today
 *     meta:        object,   // type-specific fields, see below
 *   }
 *
 * `totalPrice` and `addonTotal` are guaranteed present and correctly summed
 * on EVERY item type, which is what lets the existing cart total / grand
 * total / quotation-text-builder logic in quotation.html keep working
 * unchanged (it only ever reads `item.totalPrice`, `item.addons`,
 * `item.addonTotal`, `item.name`) — see quotation.html's
 * `updateCartTotals()` and `buildQuotationText()`.
 *
 * `meta` holds everything specific to that product type, used by the
 * (type-aware) card renderer added in Step 3:
 *
 *   type: "transfer"
 *     meta: {
 *       serviceId, destination, serviceType, rateType, vehicle, basePrice
 *     }
 *
 *   type: "attraction"
 *     meta: {
 *       attractionId, city, supplier, packageGroup, packageLabel,
 *       paxSummary: {adults, children, seniors, total_pax},
 *       breakdown: [...]   // from pricing.calculate_ticket_only()
 *     }
 *
 *   type: "attraction_bundle"
 *     meta: {
 *       attractionId, city, supplier, packageGroup, packageLabel,
 *       transferId, transferName, transferServiceType, vehicle, rateType,
 *       paxSummary: {adults, children, seniors, total_pax},
 *       ticketTotal, transferTotal,             // THB, sub-components
 *       combinedBreakdown: [...],                // from pricing.calculate_ticket_with_transfer()
 *       notes: [...]                              // e.g. "no child transfer rate" warnings
 *     }
 *
 * BACKWARD COMPATIBILITY
 * ───────────────────────
 * Cart items built by the *old* inline logic in quotation.html had no
 * `type` field. `migrateLegacyCartItem()` below normalizes any such item
 * (or any item loaded from an older saved quotation) into the new shape by
 * tagging it `type: "transfer"` and moving its transfer-specific fields
 * into `meta`, without changing `totalPrice`/`addons`/`addonTotal`. Call
 * it defensively on load; it is a no-op on items that already have `type`.
 */

// ── ID generation ──────────────────────────────────────────────────────────
function _newCartLineId() {
  // Not a product id — purely a unique key for this cart line, so the same
  // product can be added twice (e.g. two different pax mixes of the same
  // attraction) without colliding.
  return `cart_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

// ── Factory: transfer cart item ──────────────────────────────────────────────
/**
 * Build a cart item for a plain transfer (existing behaviour, now explicit
 * about `type`). This mirrors what addToCartFromDetail() built inline
 * before — same fields, same math — just reshaped into { ...common, meta }.
 *
 * @param {object} svc - the service object from allResults (has .id, .name,
 *   .destination, .service_type)
 * @param {object} selectedRate - { rateType, vehicle, price, paxCategory }
 * @param {Array<{name:string, price:number}>} addons
 */
function makeTransferCartItem(svc, selectedRate, addons = []) {
  const { rateType, vehicle, price, paxCategory } = selectedRate;
  const addonTotal = addons.reduce((sum, a) => sum + a.price, 0);

  return {
    id: _newCartLineId(),
    type: "transfer",
    name: svc.name,
    totalPrice: price + addonTotal,
    addons: [...addons],
    addonTotal,
    quantity: 1,
    meta: {
      serviceId: svc.id,
      destination: svc.destination,
      serviceType: svc.service_type,
      rateType,
      vehicle: vehicle || paxCategory || "N/A",
      basePrice: price,
    },
  };
}

// ── Factory: attraction ticket-only cart item ────────────────────────────────
/**
 * Build a cart item for an attraction ticket with NO transfer attached.
 * `pricedResult` is the response of GET /quotation/api/attraction/{id}
 * (the `.pricing` field: { attraction, breakdown, total, pax_summary }).
 *
 * @param {object} attraction - raw attraction_products row
 * @param {object} pricedResult - result of pricing.calculate_ticket_only()
 *   as returned by the API, i.e. { breakdown, total, pax_summary }
 * @param {Array<{name:string, price:number}>} addons - reserved for parity
 *   with transfers; attraction_products has no addons table today, so this
 *   is normally empty, but kept for shape consistency and future use.
 */
function makeAttractionCartItem(attraction, pricedResult, addons = []) {
  const addonTotal = addons.reduce((sum, a) => sum + a.price, 0);

  return {
    id: _newCartLineId(),
    type: "attraction",
    name: attraction.attraction_name,
    totalPrice: pricedResult.total + addonTotal,
    addons: [...addons],
    addonTotal,
    quantity: 1,
    meta: {
      attractionId: attraction.id,
      city: attraction.city,
      supplier: attraction.supplier,
      packageGroup: attraction.package_group,
      packageLabel: attraction.package_label,
      paxSummary: pricedResult.pax_summary,
      breakdown: pricedResult.breakdown,
    },
  };
}

// ── Factory: attraction + transfer bundle cart item ──────────────────────────
/**
 * Build a cart item for an attraction ticket WITH a transfer attached — the
 * "advanced" combo flow. `combinedResult` is one entry's `.combined` field
 * from GET /quotation/api/attraction/{id}/transfer-options, i.e. the output
 * of pricing.calculate_ticket_with_transfer().
 *
 * @param {object} attraction - raw attraction_products row
 * @param {object} combinedResult - one option's `.combined` from the
 *   transfer-options endpoint: { attraction, transfer, ticket_total,
 *   transfer_total, total, combined_breakdown, pax_summary, notes }
 */
function makeAttractionBundleCartItem(attraction, combinedResult) {
  return {
    id: _newCartLineId(),
    type: "attraction_bundle",
    name: `${attraction.attraction_name} + Transfer`,
    totalPrice: combinedResult.total,
    addons: [],
    addonTotal: 0,
    quantity: 1,
    meta: {
      attractionId: attraction.id,
      city: attraction.city,
      supplier: attraction.supplier,
      packageGroup: attraction.package_group,
      packageLabel: attraction.package_label,
      transferId: combinedResult.transfer.id,
      transferName: combinedResult.transfer.name,
      transferServiceType: combinedResult.transfer.service_type,
      vehicle: combinedResult.transfer.vehicle,
      rateType: combinedResult.transfer.rate_type,
      paxSummary: combinedResult.pax_summary,
      ticketTotal: combinedResult.ticket_total,
      transferTotal: combinedResult.transfer_total,
      combinedBreakdown: combinedResult.combined_breakdown,
      notes: combinedResult.notes || [],
    },
  };
}



// ── Backward-compat migration for pre-Step-2 cart items ──────────────────────
/**
 * Normalize a cart item that predates the `type` field (i.e. the old
 * transfer-only shape built directly in addToCartFromDetail()) into the
 * unified shape. Idempotent: items that already have `type` pass through
 * unchanged. Call this on every item coming from an untrusted/older source
 * (e.g. a quotation draft saved before this migration shipped).
 */
function migrateLegacyCartItem(item) {
  if (item && item.type) return item; // already unified — no-op

  return {
    id: item.id != null ? String(item.id) : _newCartLineId(),
    type: "transfer",
    name: item.name,
    totalPrice: item.totalPrice,
    addons: item.addons || [],
    addonTotal: item.addonTotal || 0,
    quantity: item.quantity || 1,
    meta: {
      serviceId: item.serviceId,
      destination: item.destination,
      serviceType: item.serviceType,
      rateType: item.rateType,
      vehicle: item.vehicle,
      basePrice: item.basePrice,
    },
  };
}

// ── Cart-wide totals (type-agnostic — works because every item has
//    totalPrice regardless of `type`) ──────────────────────────────────────
function cartSubtotal(cart) {
  return cart.reduce((sum, item) => sum + item.totalPrice, 0);
}

function cartGrandTotal(cart, commissionPercent) {
  const comm = commissionPercent || 100;
  const subtotal = cartSubtotal(cart);
  const commissionAmount = subtotal / (comm / 100);
  return Math.round(commissionAmount * 2) / 2; // round to nearest 0.5, matches existing roundToNearestHalf()
}

// Exposed as plain globals (this file is loaded via <script src>, matching
// the rest of quotation.html's non-module script style) rather than ES
// module exports, so no build step / bundler is introduced by this change.




// ── Factory: transfer + attraction bundle cart item (New Flow) ─────────────────
/**
 * Build a cart item for a transfer WITH an optional attraction attached.
 * This supports the "Transfer First" flow.
 *
 * @param {object} transferSvc - raw transfer service row
 * @param {object} selectedRate - { rateType, vehicle, price, paxCategory }
 * @param {object} attraction - raw attraction_products row
 * @param {object} pricedResult - result of pricing.calculate_ticket_only()
 * @param {Array<{name:string, price:number}>} addons
 */
function makeTransferBundleCartItem(transferSvc, selectedRate, attraction, pricedResult, addons = []) {
  const { rateType, vehicle, price, paxCategory } = selectedRate;
  const addonTotal = addons.reduce((sum, a) => sum + a.price, 0);

  const transferTotal = price + addonTotal;
  const ticketTotal = pricedResult.total;

  return {
    id: _newCartLineId(),
    type: "transfer_bundle",
    name: `${transferSvc.name} + ${attraction.attraction_name}`,
    totalPrice: transferTotal + ticketTotal,
    addons: [...addons],
    addonTotal,
    quantity: 1,
    meta: {
      // Transfer Data
      serviceId: transferSvc.id,
      destination: transferSvc.destination,
      serviceType: transferSvc.service_type,
      rateType,
      vehicle: vehicle || paxCategory || "N/A",
      basePrice: price,
      transferTotal: transferTotal,
      
      // Attraction Data
      attractionId: attraction.id,
      city: attraction.city,
      supplier: attraction.supplier,
      packageGroup: attraction.package_group,
      packageLabel: attraction.package_label,
      paxSummary: pricedResult.pax_summary,
      ticketTotal: ticketTotal,
      breakdown: pricedResult.breakdown,
    },
  };
}