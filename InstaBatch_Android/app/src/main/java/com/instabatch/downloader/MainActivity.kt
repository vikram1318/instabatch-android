package com.instabatch.downloader
import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit

class MainActivity:ComponentActivity(){
 override fun onCreate(savedInstanceState:Bundle?){super.onCreate(savedInstanceState);setContent{MaterialTheme(colorScheme=darkColorScheme(primary=Color(0xFFB6A1FF),background=Color(0xFF11131A))){Screen(this)}}}
}
@Composable fun Screen(ctx:Context){
 var server by remember{mutableStateOf("https://YOUR-RENDER-SERVICE.onrender.com")}
 val clientId = remember {
 val preferences=ctx.getSharedPreferences("instabatch",Context.MODE_PRIVATE)
 preferences.getString("client_id",null) ?: UUID.randomUUID().toString().also{preferences.edit().putString("client_id",it).apply()}
 }
 var pass by remember{mutableStateOf("")}; var links by remember{mutableStateOf(List(10){""})}
 var quality by remember{mutableStateOf("best")}; var msg by remember{mutableStateOf("Enter server URL and Instagram links")}
 var busy by remember{mutableStateOf(false)}; var progress by remember{mutableFloatStateOf(0f)}
 var logs by remember{mutableStateOf(listOf<String>())}; val scope= rememberCoroutineScope()
 Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(18.dp),verticalArrangement=Arrangement.spacedBy(10.dp)){
 Text("InstaBatch Downloader",style=MaterialTheme.typography.headlineMedium)
 Text("Batch download up to 10 videos")
 OutlinedTextField(server,{server=it},Modifier.fillMaxWidth(),label={Text("Backend HTTPS URL")},singleLine=true)
 OutlinedTextField(pass,{pass=it},Modifier.fillMaxWidth(),label={Text("App password, if enabled")},singleLine=true)
 links.forEachIndexed{i,v->OutlinedTextField(v,{x->links=links.toMutableList().also{it[i]=x}},Modifier.fillMaxWidth(),label={Text("Instagram link ${i+1}")},singleLine=true)}
 Text("Quality")
 Row(horizontalArrangement=Arrangement.spacedBy(6.dp)){listOf("best","1080p","720p","480p").forEach{q->FilterChip(quality==q,{quality=q},{Text(q)})}}
 Button(enabled=!busy,onClick={
 val base=server.trim().trimEnd('/'); val items=links.map{it.trim()}.filter{it.isNotEmpty()}.distinct()
 if(!base.startsWith("https://")){msg="Enter a valid HTTPS backend URL";return@Button}
 if(items.isEmpty()){msg="Enter at least one link";return@Button}
 busy=true;progress=0f;logs=emptyList();msg="Starting batch…"
 scope.launch{try{
 val api=Api(base,clientId);val id=withContext(Dispatchers.IO){api.submit(pass,items,quality)}
 while(true){delay(2000);val s=withContext(Dispatchers.IO){api.status(id,pass)}
 val total=s.optInt("total",items.size).coerceAtLeast(1);val done=s.optInt("done");progress=done.toFloat()/total
 val a=s.optJSONArray("logs");logs=if(a==null) emptyList() else (0 until a.length()).map{a.optString(it)}.takeLast(60)
 msg="Progress: $done / $total";if(s.optString("status")=="done"||s.optString("status")=="error")break}
 msg="Batch finished. Tap Save videos to phone."
 }catch(e:Exception){msg="Error: ${e.message}"}finally{busy=false}}
 },modifier=Modifier.fillMaxWidth()){Text(if(busy)"Processing…" else "Download batch")}
 LinearProgressIndicator({progress},Modifier.fillMaxWidth());Text(msg)
 OutlinedButton(onClick={scope.launch{try{val api=Api(server.trim().trimEnd('/'),clientId);val arr=withContext(Dispatchers.IO){api.files(pass)}
 if(arr.length()==0)msg="No files are ready yet." else {for(i in 0 until arr.length()){val f=arr.getJSONObject(i);withContext(Dispatchers.IO){api.save(ctx,f.optString("url"),f.optString("filename"),pass)}};msg="Transfers queued in Android Downloads."}
 }catch(e:Exception){msg="Could not fetch files: ${e.message}"}}},modifier=Modifier.fillMaxWidth()){Text("Save downloaded videos to phone")}
 logs.forEach{Text(it,style=MaterialTheme.typography.bodySmall,color=Color.LightGray)}
 Text("Only save videos you own or have permission to download.",color=Color.Gray,style=MaterialTheme.typography.bodySmall)
 }
}
class Api(private val base:String,private val clientId:String){
 private var authenticated=false
 private val jar=object:CookieJar{private val saved=mutableListOf<Cookie>()
 override fun saveFromResponse(url:HttpUrl,cookies:List<Cookie>){synchronized(saved){saved.removeAll{old->cookies.any{it.name==old.name&&it.domain==old.domain}};saved.addAll(cookies)}}
 override fun loadForRequest(url:HttpUrl)=synchronized(saved){saved.filter{it.matches(url)}}
 }
 private val client=OkHttpClient.Builder().cookieJar(jar).addInterceptor{chain->chain.proceed(chain.request().newBuilder().header("X-Client-ID",clientId).build())}.connectTimeout(25,TimeUnit.SECONDS).readTimeout(60,TimeUnit.SECONDS).build()
 private fun req(path:String)=Request.Builder().url(base+path)
 private fun login(p:String){if(authenticated)return;val r=req("/login").post(FormBody.Builder().add("password",p).build()).build();client.newCall(r).execute().use{if(!it.isSuccessful)error("Login failed: HTTP ${it.code}")};authenticated=true}
 fun submit(p:String,urls:List<String>,q:String):String{login(p);val body=JSONObject().put("urls",JSONArray(urls)).put("quality",q).put("workers",3).toString().toRequestBody("application/json".toMediaType());val r=req("/api/download").post(body).build();return client.newCall(r).execute().use{if(!it.isSuccessful)error("Download API HTTP ${it.code}: ${it.body?.string()}");JSONObject(it.body!!.string()).getString("job_id")}}
 fun status(id:String,p:String):JSONObject{login(p);val r=req("/api/status/$id").get().build();return client.newCall(r).execute().use{if(!it.isSuccessful)error("Status HTTP ${it.code}");JSONObject(it.body!!.string())}}
 fun files(p:String):JSONArray{login(p);val r=req("/api/files").get().build();return client.newCall(r).execute().use{if(!it.isSuccessful)error("Files HTTP ${it.code}");JSONArray(it.body!!.string())}}
 fun save(ctx:Context,path:String,name:String,p:String){val url=base+path;val r=DownloadManager.Request(Uri.parse(url)).setTitle(name).setDescription("InstaBatch video").setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED).setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS,name)
 val cookie=jar.loadForRequest(url.toHttpUrl()).joinToString("; "){it.toString()};if(cookie.isNotBlank())r.addRequestHeader("Cookie",cookie)
 (ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(r)}
}
