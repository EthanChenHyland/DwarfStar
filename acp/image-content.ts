/** Binary image fields must never run through a textual credential redactor. */
export function forTextRedaction(content:any){
 if(!Array.isArray(content))return content;
 return content.map((block:any)=>block?.type==='image'?{type:'image',mimeType:block.mimeType}:block);
}
export function restoreImageContent(original:any,redacted:any){
 if(!Array.isArray(original)||!Array.isArray(redacted)||original.length!==redacted.length)throw Error('Image redaction shape mismatch');
 return redacted.map((block:any,index:number)=>original[index]?.type==='image'?original[index]:block);
}
export function imageFacts(messages:any[]=[]){
 let count=0,bytes=0;
 for(const message of messages){for(const block of Array.isArray(message?.content)?message.content:[]){
  if(block?.type==='image'){count++;bytes+=typeof block.data==='string'?Buffer.byteLength(block.data,'base64'):0;}
  else if(block?.type==='image_url'&&typeof block.image_url?.url==='string'&&block.image_url.url.startsWith('data:image/')){count++;bytes+=Buffer.byteLength(block.image_url.url.split(',')[1]??'','base64');}
 }}return {image_count:count,image_bytes:bytes};
}
