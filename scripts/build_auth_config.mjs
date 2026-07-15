#!/usr/bin/env node
/** 生成公开仓库可保存的加密登录配置；绝不把 token 或密码写入输出。 */
import {webcrypto} from 'node:crypto';
import {writeFile} from 'node:fs/promises';
import {readFileSync} from 'node:fs';

const {subtle}=webcrypto;
const username=(process.env.DASHBOARD_LOGIN_USERNAME||'').trim().toLowerCase();
const secretFrom=(valueName,fileName)=>process.env[valueName]||(process.env[fileName]?readFileSync(process.env[fileName],'utf8').trim():'');
const password=secretFrom('DASHBOARD_LOGIN_PASSWORD','DASHBOARD_LOGIN_PASSWORD_FILE');
const token=secretFrom('DASHBOARD_GITHUB_TOKEN','DASHBOARD_GITHUB_TOKEN_FILE');
const owner=process.env.DASHBOARD_GITHUB_OWNER||'datiancailty';
const repo=process.env.DASHBOARD_GITHUB_REPO||'stock-dashboard';
const output=process.env.DASHBOARD_AUTH_OUTPUT||'data/auth-config.json';
if(!username||password.length<16||!token)throw new Error('需要用户名、至少16位强密码和仓库限定的GitHub fine-grained token');
const enc=new TextEncoder();
const b64=bytes=>Buffer.from(bytes).toString('base64');
const hex=bytes=>Buffer.from(bytes).toString('hex');
const salt=webcrypto.getRandomValues(new Uint8Array(16));
const iv=webcrypto.getRandomValues(new Uint8Array(12));
const material=await subtle.importKey('raw',enc.encode(password),'PBKDF2',false,['deriveKey']);
const key=await subtle.deriveKey({name:'PBKDF2',salt,iterations:600000,hash:'SHA-256'},material,{name:'AES-GCM',length:256},false,['encrypt']);
const plaintext=enc.encode(JSON.stringify({token,owner,repo,createdAt:new Date().toISOString()}));
const ciphertext=await subtle.encrypt({name:'AES-GCM',iv},key,plaintext);
const usernameHash=hex(new Uint8Array(await subtle.digest('SHA-256',enc.encode(username))));
await writeFile(output,JSON.stringify({version:1,algorithm:'PBKDF2-SHA256/AES-256-GCM',iterations:600000,usernameHash,salt:b64(salt),iv:b64(iv),ciphertext:b64(new Uint8Array(ciphertext))},null,2)+'\n',{mode:0o644});
console.log(JSON.stringify({ok:true,output,usernameConfigured:true,passwordStored:false,tokenStoredPlaintext:false}));
