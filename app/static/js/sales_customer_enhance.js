function customerSuggestionsBox(){
  let box=document.getElementById('clientSuggestions');
  if(!box){
    box=document.createElement('div');
    box.id='clientSuggestions';
    box.style.cssText='display:none;margin-top:6px;border:1px solid var(--border);border-radius:12px;overflow:hidden;background:#fff;box-shadow:0 10px 24px rgba(15,23,42,.12);max-height:180px;overflow-y:auto';
    document.getElementById('clientDocument')?.closest('label')?.appendChild(box);
  }
  return box;
}

function moveCarnetFirst(){
  const doc=document.getElementById('clientDocument');
  const section=doc?.closest('section');
  const label=doc?.closest('label');
  const title=section?.querySelector('h3');
  if(section&&label&&title&&title.nextSibling!==label){
    title.insertAdjacentElement('afterend',label);
  }
  customerSuggestionsBox();
}

function resetCustomerFields(){
  ['clientDocument','clientName','clientPhone','clientCity','shippingDetail'].forEach(id=>{const el=document.getElementById(id); if(el)el.value='';});
  const tienda=document.querySelector('[name="tipo_entrega"][value="TIENDA"]');
  if(tienda)tienda.checked=true;
  const box=document.getElementById('clientSuggestions');
  if(box){box.innerHTML='';box.style.display='none';}
  if(typeof toggleShippingFields==='function')toggleShippingFields();
}

function fillCustomer(customer){
  const doc=document.getElementById('clientDocument');
  const name=document.getElementById('clientName');
  const phone=document.getElementById('clientPhone');
  const city=document.getElementById('clientCity');
  const detail=document.getElementById('shippingDetail');
  if(doc)doc.value=customer.carnet||'';
  if(name)name.value=customer.nombre||'';
  if(phone)phone.value=customer.celular||'';
  if(city&&customer.ciudad)city.value=customer.ciudad;
  if(detail&&customer.detalle_envio)detail.value=customer.detalle_envio;
  const box=customerSuggestionsBox();
  box.innerHTML=`<div style="padding:8px 10px;font-size:12px;color:var(--muted)">Cliente seleccionado · Compras acumuladas: Bs ${Number(customer.total_compras||0).toFixed(2)}</div>`;
  box.style.display='block';
}

async function searchCustomersByDocument(){
  const doc=document.getElementById('clientDocument');
  const q=(doc?.value||'').trim();
  const box=customerSuggestionsBox();
  if(q.length<2){box.innerHTML='';box.style.display='none';return;}
  try{
    const response=await fetch(`/ventas/api/clientes?q=${encodeURIComponent(q)}`,{cache:'no-store',headers:{Accept:'application/json'}});
    if(!response.ok)return;
    const rows=await response.json();
    if(!Array.isArray(rows)||!rows.length){
      box.innerHTML='<div style="padding:8px 10px;font-size:12px;color:var(--muted)">Sin registros anteriores · compras acumuladas: Bs 0.00</div>';
      box.style.display='block';
      return;
    }
    box.innerHTML=rows.map((row,index)=>`<button type="button" data-customer-index="${index}" style="display:block;width:100%;border:0;border-bottom:1px solid var(--border);background:#fff;text-align:left;padding:8px 10px"><strong>${esc(row.carnet||'Sin carnet')}</strong> · ${esc(row.nombre||'Cliente')}<small style="display:block;color:var(--muted)">Cel: ${esc(row.celular||'-')} · Compras: Bs ${Number(row.total_compras||0).toFixed(2)}</small></button>`).join('');
    box.__customers=rows;
    box.style.display='block';
  }catch(error){}
}

function enhancedClientPayload(){
  return {
    carnet:(document.getElementById('clientDocument')?.value||'').trim(),
    nombre:(document.getElementById('clientName')?.value||'').trim(),
    celular:(document.getElementById('clientPhone')?.value||'').trim(),
    tipo_entrega:document.querySelector('[name="tipo_entrega"]:checked')?.value||'TIENDA',
    ciudad_destino:(document.getElementById('clientCity')?.value||'').trim(),
    detalle_envio:(document.getElementById('shippingDetail')?.value||'').trim(),
  };
}

function saleReceiptPreview(data, cart, client, method, receiptUrl){
  const comprobante=data.numero_comprobante?String(data.numero_comprobante).padStart(6,'0'):(data.numero_venta||data.venta_id);
  const rows=cart.map(item=>`<div style="display:flex;justify-content:space-between;gap:8px;border-bottom:1px dashed #e5e7eb;padding:3px 0"><span>${item.cantidad} × ${esc(item.nombre)}${item.es_yapa?' · YAPA':''}</span><span>${saleMoney(item.es_yapa?0:item.precio_unitario*item.cantidad)}</span></div>`).join('');
  const delivery=client.tipo_entrega==='ENVIO'?`Envío${client.ciudad_destino?' · '+esc(client.ciudad_destino):''}${client.detalle_envio?' · '+esc(client.detalle_envio):''}`:'Entrega en tienda';
  return `<div class="sale-receipt-preview" style="border:1px solid var(--border);border-radius:14px;padding:12px;background:#fff;margin-top:10px">
    <p class="eyebrow" style="margin:0 0 4px">Vista de comprobante</p>
    <h3 style="margin:0 0 8px">Comprobante ${esc(comprobante)}</h3>
    <div style="font-size:12px;line-height:1.45;margin-bottom:8px">
      <div><strong>Pago:</strong> ${esc(method||'-')}</div>
      ${client.carnet?`<div><strong>Carnet:</strong> ${esc(client.carnet)}</div>`:''}
      ${client.nombre?`<div><strong>Cliente:</strong> ${esc(client.nombre)}</div>`:''}
      ${client.celular?`<div><strong>Celular:</strong> ${esc(client.celular)}</div>`:''}
      <div><strong>Entrega:</strong> ${delivery}</div>
    </div>
    <div style="font-size:12px">${rows}</div>
    <div style="display:flex;justify-content:space-between;margin-top:8px;font-weight:700"><span>Total</span><span>${saleMoney(data.total)}</span></div>
    <div style="margin-top:10px">Venta confirmada por ${saleMoney(data.total)}.<br><a href="${receiptUrl}" target="_blank">Imprimir comprobante</a> · <a href="/ventas/${data.venta_id}/comprobante-pdf" target="_blank">Abrir PDF</a></div>
  </div>`;
}

if(typeof clientPayload==='function') clientPayload=enhancedClientPayload;
if(typeof openCheckout==='function'){
  openCheckout=function(){
    if(!cashIsOpen()){alert('Debes abrir caja antes de confirmar ventas.');document.querySelector('#cashOpenModal')?.classList.add('is-open');return;}
    if(!saleState.cart.length){alert('Añade productos antes de confirmar.');return;}
    resetCustomerFields();
    saleState.idempotencyKey=newIdempotencyKey();
    const preview=document.getElementById('checkoutPreview');
    preview.innerHTML=saleState.cart.map(item=>`<div class="checkout-row"><span>${item.cantidad} × ${esc(item.nombre)} (${esc(item.presentacion)})${item.es_yapa?' · YAPA':''}</span><span>${saleMoney(item.es_yapa?0:item.precio_unitario*item.cantidad)}</span></div>`).join('')+`<div class="checkout-row"><span>Total</span><span>${saleMoney(totals().total)}</span></div>`;
    document.getElementById('saleResult').hidden=true;
    document.getElementById('saleResult').innerHTML='';
    document.getElementById('confirmSaleBtn').disabled=false;
    document.getElementById('confirmSaleBtn').textContent='Confirmar pago';
    updateSellerChips();
    toggleShippingFields();
    document.getElementById('checkoutModal').classList.add('is-open');
    setTimeout(()=>document.getElementById('clientDocument')?.focus(),80);
  };
}

if(typeof confirmSale==='function'){
  confirmSale=async function(){
    if(!cashIsOpen()){alert('Debes abrir caja antes de confirmar ventas.');return;}
    if(!saleState.paymentMethod){alert('Selecciona un método de pago.');return;}
    const button=document.getElementById('confirmSaleBtn');
    const confirmedCart=saleState.cart.map(item=>({...item}));
    const confirmedClient=clientPayload();
    const confirmedMethod=saleState.paymentMethod;
    button.disabled=true;button.textContent='Procesando...';
    try{
      const response=await fetch('/ventas/confirmar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items:saleState.orderId?[]:payloadItems(),orden_id:saleState.orderId,metodo_pago:saleState.paymentMethod,idempotency_key:saleState.idempotencyKey,vendedor_id:saleState.selectedSellerId||null,cliente:confirmedClient})});
      const data=await response.json();
      if(!response.ok)throw new Error(data.error||'No se pudo confirmar la venta.');
      const receiptUrl=`/ventas/${data.venta_id}/comprobante`;
      const result=document.getElementById('saleResult');
      result.hidden=false;
      result.innerHTML=saleReceiptPreview(data,confirmedCart,confirmedClient,confirmedMethod,receiptUrl);
      updateCashView(data.caja);renderPendingOrders(data.pending_orders);
      button.textContent='Venta confirmada';button.disabled=true;
      saleState.cart=[];saleState.orderId=null;resetSellerToDefault();renderCart();searchProducts();
    }catch(error){alert(error.message);button.disabled=false;button.textContent='Confirmar pago';}
  };
}

document.addEventListener('DOMContentLoaded',()=>{
  moveCarnetFirst();
  const doc=document.getElementById('clientDocument');
  let timer=null;
  doc?.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(searchCustomersByDocument,220);});
  customerSuggestionsBox()?.addEventListener('click',event=>{
    const button=event.target.closest('[data-customer-index]');
    if(!button)return;
    const row=customerSuggestionsBox().__customers?.[Number(button.dataset.customerIndex)];
    if(row)fillCustomer(row);
  });
});
