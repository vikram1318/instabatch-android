plugins { id("com.android.application"); id("org.jetbrains.kotlin.android"); id("org.jetbrains.kotlin.plugin.compose") }
val releaseSigning = listOf(
 System.getenv("INSTABATCH_KEYSTORE_FILE"),
 System.getenv("INSTABATCH_KEYSTORE_PASSWORD"),
 System.getenv("INSTABATCH_KEY_ALIAS"),
 System.getenv("INSTABATCH_KEY_PASSWORD")
)
val hasReleaseSigning = releaseSigning.all { !it.isNullOrBlank() }
android { namespace = "com.instabatch.downloader"; compileSdk = 35
 defaultConfig { applicationId = "com.instabatch.downloader"; minSdk = 26; targetSdk = 35; versionCode = 1; versionName = "1.0.0" }
 compileOptions { sourceCompatibility = JavaVersion.VERSION_1_8; targetCompatibility = JavaVersion.VERSION_1_8 }
 kotlinOptions { jvmTarget = JavaVersion.VERSION_1_8.toString() }
 signingConfigs {
  if (hasReleaseSigning) create("release") {
   storeFile = file(releaseSigning[0]!!)
   storePassword = releaseSigning[1]
   keyAlias = releaseSigning[2]
   keyPassword = releaseSigning[3]
  }
 }
 buildTypes { getByName("release") { if (hasReleaseSigning) signingConfig = signingConfigs.getByName("release") } }
 buildFeatures { compose = true } }
dependencies {
 implementation(platform("androidx.compose:compose-bom:2024.12.01"))
 implementation("androidx.activity:activity-compose:1.10.0")
 implementation("androidx.compose.ui:ui")
 implementation("androidx.compose.ui:ui-tooling-preview")
 implementation("androidx.compose.material3:material3")
 implementation("androidx.compose.material:material-icons-extended")
 implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
 implementation("com.squareup.okhttp3:okhttp:4.12.0")
 implementation("org.json:json:20240303")
 debugImplementation("androidx.compose.ui:ui-tooling")
}
