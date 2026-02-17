import { TwelveLabs } from "twelvelabs-js";
import dotenv from "dotenv";
import { readFileSync } from "fs";
import { MongoClient } from "mongodb";
import OpenAI from "openai";
import fixJSONPrompt from "./prompts/1.fix-json.js";

dotenv.config();

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
const uri = "mongodb://localhost:27017/creator-kb";

const connectToMongo = async () => {
  console.log("Connecting to MongoDB...");
  const client = new MongoClient(uri);
  await client.connect();
  console.log("Connected to MongoDB");
  const db = client.db("creator-kb");
  return db.collection("raw");
};

const readPrompts = () => {
  console.log("Reading prompts...");
  const prompts = [
    readFileSync("./prompts/1.comprehensive_description.txt", "utf-8"),
    readFileSync("./prompts/1.categorization.txt", "utf-8"),
    readFileSync("./prompts/1.timeline.txt", "utf-8"),
    readFileSync("./prompts/1.main-ideas.txt", "utf-8"),
    readFileSync("./prompts/1.vibe.txt", "utf-8"),
    readFileSync("./prompts/1.music-audio.txt", "utf-8"),
    readFileSync("./prompts/2.script.txt", "utf-8")
  ];
  console.log("Prompts read successfully");
  return prompts;
};

const extractAnalyzeText = (response) => {
  if (!response) return "";

  if (typeof response.data === "string") return response.data;
  if (typeof response.text === "string") return response.text;

  if (response.data && typeof response.data === "object") {
    if (typeof response.data.text === "string") return response.data.text;
    if (typeof response.data.summary === "string") return response.data.summary;
    if (typeof response.data.content === "string") return response.data.content;
  }

  return JSON.stringify(response.data ?? response);
};

const extractPageData = (page) => {
  if (!page) return [];
  if (Array.isArray(page)) return page;
  if (Array.isArray(page.data)) return page.data;
  return [];
};

const listAllIndexVideos = async (client, indexId) => {
  // v0.4.x client shape
  if (client?.index?.video?.list) {
    return await client.index.video.list(indexId);
  }

  // v1-style shape (future compatible)
  const allVideos = [];
  const pageLimit = 50;
  let page = 1;

  while (true) {
    let response;
    try {
      response = await client.indexes.videos.list(indexId, { page, page_limit: pageLimit });
    } catch {
      response = await client.indexes.videos.list(indexId);
    }

    const videos = extractPageData(response);
    allVideos.push(...videos);

    const totalPages =
      response?.page_info?.total_page ??
      response?.pageInfo?.totalPage ??
      null;

    if (!totalPages || page >= totalPages || videos.length === 0) {
      break;
    }
    page += 1;
  }

  return allVideos;
};

const analyzeVideoText = async (client, videoId, prompt, temperature = 0.8) => {
  // v1-style shape (future compatible)
  if (typeof client?.analyze === "function") {
    return await client.analyze({
      video_id: videoId,
      prompt
    });
  }

  // v0.4.x shape
  if (client?.generate?.text) {
    return await client.generate.text(videoId, prompt, temperature, {});
  }

  throw new Error("Unsupported TwelveLabs client API shape for text generation");
};

const cleanData = (data) => {
  console.log("Cleaning data...");
  const cleanedData = data
    .replace(/\n/g, "")
    .replace(/\+/g, "")
    .replace(/`/g, "'");
  console.log("Data cleaned");
  return cleanedData;
};

const updateCollection = async (collection, mediaId, data) => {
  const cleanedData = cleanData(data);
  try {
    console.log(`Parsing data for mediaId: ${mediaId}`);
    const parsedData = JSON.parse(cleanedData);
    console.log("Data parsed successfully");

    // Add timestamps
    const timestamp = new Date();
    await collection.findOneAndUpdate(
      { mediaId },
      {
        $set: {
          ...parsedData,
          updatedAt: timestamp,
        },
        $setOnInsert: {
          createdAt: timestamp,
        },
      },
      { upsert: true }
    );

    console.log(`Collection updated for mediaId: ${mediaId}`);
    return parsedData;
  } catch (error) {
    if (error instanceof SyntaxError && error.message.includes("JSON")) {
      console.log("JSON parse error detected, attempting to fix JSON...");
      const fixedJSONPrompt = fixJSONPrompt(cleanedData);
      const response = await openai.chat.completions.create({
        model: "gpt-4o-mini",
        messages: [{ role: "user", content: fixedJSONPrompt }],
        temperature: 0,
        response_format: { type: "json_object" },
      });

      console.log("OPENAI response", response);

      const fixedJson = response.choices[0].message.content;
      const parsedFixedJson = JSON.parse(fixedJson);

      await collection.findOneAndUpdate(
        { mediaId },
        { $set: { ...parsedFixedJson } },
        { upsert: true }
      );

      console.log(`Collection updated with fixed JSON for mediaId: ${mediaId}`);
      return parsedFixedJson;
    } else {
      console.error("Error updating collection:", error);
      throw error; // Re-throw the error if it's not a JSON parse error
    }
  }
};

const updateExperimentCollection = async (collection, mediaId, promptPath, data) => {
  const cleanedData = cleanData(data);
  try {
    console.log(`Parsing experimental data for mediaId: ${mediaId}`);
    const parsedData = JSON.parse(cleanedData);
    console.log("Data parsed successfully");

    // Get current version number for this mediaId + promptPath combination
    const lastExperiment = await collection
      .find({ mediaId, promptPath })
      .sort({ version: -1 })
      .limit(1)
      .toArray();
    
    const version = lastExperiment.length > 0 ? lastExperiment[0].version + 1 : 1;

    // Store experiment with version
    await collection.insertOne({
      mediaId,
      promptPath,
      version,
      data: parsedData,
      createdAt: new Date(),
    });

    console.log(`Experiment stored for mediaId: ${mediaId}, version: ${version}`);
    return { ...parsedData, version };
  } catch (error) {
    if (error instanceof SyntaxError && error.message.includes("JSON")) {
      console.log("JSON parse error detected, attempting to fix JSON...");
      const fixedJSONPrompt = fixJSONPrompt(cleanedData);
      const response = await openai.chat.completions.create({
        model: "gpt-4o-mini",
        messages: [{ role: "user", content: fixedJSONPrompt }],
        temperature: 0,
        response_format: { type: "json_object" },
      });

      const fixedJson = response.choices[0].message.content;
      const parsedFixedJson = JSON.parse(fixedJson);
      
      // Store fixed JSON as a new version
      const lastExperiment = await collection
        .find({ mediaId, promptPath })
        .sort({ version: -1 })
        .limit(1)
        .toArray();
      
      const version = lastExperiment.length > 0 ? lastExperiment[0].version + 1 : 1;

      await collection.insertOne({
        mediaId,
        promptPath,
        version,
        data: parsedFixedJson,
        createdAt: new Date(),
      });

      console.log(`Fixed JSON experiment stored for mediaId: ${mediaId}, version: ${version}`);
      return { ...parsedFixedJson, version };
    } else {
      console.error("Error storing experiment:", error);
      throw error;
    }
  }
};

const testPrompt = async (videoId, promptPath) => {
  try {
    const client = new TwelveLabs({ apiKey: process.env.TWELVE_LABS_API_KEY });
    const mongoClient = new MongoClient(uri);
    await mongoClient.connect();
    
    const db = mongoClient.db("creator-kb");
    const experimentsCollection = db.collection("experiments");
    
    console.log(`Reading prompt from ${promptPath}...`);
    const prompt = readFileSync(promptPath, "utf-8");
    
    console.log(`Generating text for videoID: ${videoId} with prompt: ${prompt}`);
    const res = await analyzeVideoText(client, videoId, prompt, 0.5);
    console.log(`Text generation completed for video ${videoId}`);
    
    console.log(`Storing experiment result...`);
    const result = await updateExperimentCollection(
      experimentsCollection,
      videoId,
      promptPath,
      extractAnalyzeText(res)
    );
    
    console.log(`Experiment stored successfully:`);
    console.log(`- Video ID: ${videoId}`);
    console.log(`- Prompt: ${promptPath}`);
    console.log(`- Version: ${result.version}`);
    
    await mongoClient.close();
    return result;
  } catch (error) {
    console.error("Error testing prompt:", error);
    throw error;
  }
};

const main = async () => {
  const args = process.argv.slice(2);
  
  if (args.length === 0) {
    // Run the original full analysis
    await generateText();
  } else if (args.length === 2) {
    // Run single prompt test
    const [videoId, promptPath] = args;
    await testPrompt(videoId, promptPath);
  } else {
    console.log("Usage:");
    console.log("Full analysis: node index.js");
    console.log("Single prompt test: node index.js <videoId> <promptPath>");
    process.exit(1);
  }
};

const generateText = async () => {
  try {
    const collection = await connectToMongo();
    const client = new TwelveLabs({ apiKey: process.env.TWELVE_LABS_API_KEY });
    const prompts = readPrompts();
    const videos = await listAllIndexVideos(client, "6743b77b12e44d1c53a44ab5");
    const videoIDs = videos.map((video) => video.id);

    const videosMongo = await collection.find({}, { mediaId: 1 }).toArray();
    const existingIDsMongo = videosMongo.map((v) => v.mediaId);

    console.log(`Processing ${videoIDs.length} videos...`);

    for (const currVideoID of videoIDs) {
      console.log(`Checking video ID: ${currVideoID}`);
      if (existingIDsMongo.includes(currVideoID)) {
        console.log(
          `Video ${currVideoID} already exists in MongoDB, skipping...`
        );
        continue;
      }
      console.log(
        `Processing ${prompts.length} prompts for video ${currVideoID}`
      );
      for (const prompt of prompts) {
        console.log(
          `Generating text for videoID: ${currVideoID} with prompt: ${prompt}`
        );
        const res = await analyzeVideoText(client, currVideoID, prompt, 0.8);
        console.log(`Text generation completed for video ${currVideoID}`);
        console.log(`Updating MongoDB collection with generated text...`);
        await updateCollection(collection, currVideoID, extractAnalyzeText(res));
        console.log(
          `MongoDB collection updated successfully for video ${currVideoID}`
        );
      }
      console.log(`Completed processing all prompts for video ${currVideoID}`);
    }
    console.log(`Finished processing all videos`);
  } catch (error) {
    console.error("Error generating text:", error);
  }
};

main();
