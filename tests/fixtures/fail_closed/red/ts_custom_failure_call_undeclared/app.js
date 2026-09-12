async function save(){try{await fetch('/save');}catch(e){sendReply(res, 500, {error:e.message});}}
